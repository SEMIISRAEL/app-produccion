import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from google.oauth2 import service_account
from datetime import datetime, timedelta
import time
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from gspread.utils import rowcol_to_a1

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Gestor SEMI - Tablet", layout="wide", page_icon="🏗️")

# --- IDs FIJOS ---
ID_VEHICULOS = "19PWpeCz8pl5NEDpK-omX5AdrLuJgOPrn6uSjtUGomY8"
ID_CONFIG_PROD = "1uCu5pq6l1CjqXKPEkGkN-G5Z5K00qiV9kR_bGOii6FU"

# ==========================================
#            LOGIN Y ESTADO
# ==========================================
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = True
    st.session_state.user_name = "Usuario Tablet"
    st.session_state.user_role = "encargado"

# Variables Globales
if 'ID_ROSTER_ACTIVO' not in st.session_state: st.session_state.ID_ROSTER_ACTIVO = None
if 'TRAMO_ACTIVO' not in st.session_state: st.session_state.TRAMO_ACTIVO = None
if 'ARCH_PROD' not in st.session_state: st.session_state.ARCH_PROD = None
if 'ARCH_BACKUP' not in st.session_state: st.session_state.ARCH_BACKUP = None
if 'veh_glob' not in st.session_state: st.session_state.veh_glob = None
if 'lista_sel' not in st.session_state: st.session_state.lista_sel = []
if 'prod_dia' not in st.session_state: st.session_state.prod_dia = {}

# VARIABLES CHECKBOXES (ESTADO DE LA GUI)
if 'chk_giros' not in st.session_state: st.session_state.chk_giros = False
if 'chk_aisl' not in st.session_state: st.session_state.chk_aisl = False
if 'chk_comp' not in st.session_state: st.session_state.chk_comp = False
if 'last_item_loaded' not in st.session_state: st.session_state.last_item_loaded = None

def on_completo_change():
    if st.session_state.chk_comp:
        st.session_state.chk_giros = True
        st.session_state.chk_aisl = True

# ==========================================
#            CONEXIÓN Y HERRAMIENTAS
# ==========================================
@st.cache_resource
def get_gspread_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def conectar_flexible(referencia):
    client = get_gspread_client()
    try: return client.open_by_key(referencia)
    except:
        try: return client.open(referencia)
        except:
            try: return client.open(referencia.replace(".xlsx", ""))
            except: return None

def safe_val(lista, indice):
    idx_py = indice - 1
    if idx_py < len(lista): return lista[idx_py]
    return None

def leer_nota_directa(nombre_archivo, nombre_hoja, fila, col):
    """Lee la nota de una celda para ver pendientes antiguos (legacy)."""
    try:
        sh = conectar_flexible(nombre_archivo)
        ws = sh.worksheet(nombre_hoja)
        return ws.cell(fila, col).note or ""
    except: return ""

# ------------------------------------------------------------------
#  FUNCIONES DE INTELIGENCIA VISUAL (EL ROBOT)
# ------------------------------------------------------------------

def cambiar_formato_google(ws, fila, col, tipo_estilo):
    """
    LA MANO DEL ROBOT: Escribe usando 'tinta invisible' (formato).
    """
    try:
        if tipo_estilo == "GIROS":
            # Courier New + Rojo + Negrita = FALTA GIROS
            user_format = {"textFormat": {"fontFamily": "Courier New", "foregroundColor": {"red": 1.0, "green": 0.0, "blue": 0.0}, "bold": True}}
        elif tipo_estilo == "AISLADORES":
            # Times New Roman + Azul + Negrita = FALTA AISLADORES
            user_format = {"textFormat": {"fontFamily": "Times New Roman", "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 1.0}, "bold": True}}
        else:
            # Arial + Negro = NORMAL (COMPLETO)
            user_format = {"textFormat": {"fontFamily": "Arial", "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}, "bold": False}}

        body = {
            "requests": [{
                "repeatCell": {
                    "range": {"sheetId": ws.id, "startRowIndex": fila - 1, "endRowIndex": fila, "startColumnIndex": col - 1, "endColumnIndex": col},
                    "cell": {"userEnteredFormat": user_format},
                    "fields": "userEnteredFormat(textFormat)"
                }
            }]
        }
        ws.spreadsheet.batch_update(body)
        return True
    except Exception as e:
        print(f"Error formato: {e}")
        return False

def detectar_estilo_celda(ws, fila, col):
    """
    EL OJO DEL ROBOT: Lee el formato para saber qué falta, aunque haya fecha.
    """
    try:
        nombre_hoja = ws.title
        rango = f"{nombre_hoja}!{rowcol_to_a1(fila, col)}"
        
        # Petición especial a la API para traer metadatos
        res = ws.spreadsheet.fetch_sheet_metadata(params={'includeGridData': True, 'ranges': [rango]})
        
        try:
            celda_data = res['sheets'][0]['data'][0]['rowData'][0]['values'][0]
            formato = celda_data.get('userEnteredFormat', {}).get('textFormat', {})
            font_family = formato.get('fontFamily', 'Arial')
            
            if 'Courier' in font_family: return "GIROS"      # Detectamos código rojo
            elif 'Times' in font_family: return "AISLADORES" # Detectamos código azul
            else: return "NORMAL"
        except (IndexError, KeyError): return "NORMAL"
    except Exception as e: return "NORMAL"

# ==========================================
#       CARGA MASIVA Y CONFIG
# ==========================================
@st.cache_data(ttl=300) 
def cargar_datos_completos_hoja(nombre_archivo, nombre_hoja):
    sh = conectar_flexible(nombre_archivo)
    if not sh: return None
    try:
        ws = sh.worksheet(nombre_hoja)
        todos_los_datos = ws.get_all_values() 
        datos_procesados = {}
        for i, fila in enumerate(todos_los_datos):
            if not fila: continue
            item_id = str(fila[0]).strip()
            if len(item_id) > 2 and "ITEM" not in item_id.upper() and "HR TRACK" not in item_id.upper():
                datos_procesados[item_id] = {"fila_excel": i + 1, "datos": fila}
        return datos_procesados
    except: return None

@st.cache_data(ttl=300)
def buscar_archivos_roster():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=['https://www.googleapis.com/auth/drive'])
        service = build('drive', 'v3', credentials=creds)
        results = service.files().list(q="name contains 'Roster' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false", fields="files(id, name)", orderBy="name desc").execute()
        return {f['name']: f['id'] for f in results.get('files', [])}
    except: return {}

@st.cache_data(ttl=600)
def cargar_config_prod():
    sh = conectar_flexible(ID_CONFIG_PROD)
    if not sh: return {}
    try:
        datos = sh.sheet1.get_all_values()
        config = {}
        for row in datos:
            if len(row) >= 3 and row[0] and row[1]: 
                tramo = row[0].strip()
                archivo_principal = row[1].strip()
                archivo_backup = row[2].strip() if len(row) > 2 else "" 
                if tramo and archivo_principal:
                    config[tramo] = (archivo_principal, archivo_backup)
            elif row and row[0].lower() in ["tramo", "seccion"]: continue 
        return config
    except: return {}

# ==========================================
#       SIDEBAR
# ==========================================
with st.sidebar:
    st.write(f"👤 **{st.session_state.user_name.upper()}**")
    if st.button("Cerrar Sesión"):
        st.session_state.logged_in = False
        st.rerun()
    st.markdown("---")
    
    st.caption("📅 ROSTER ACTIVO")
    archivos_roster = buscar_archivos_roster()
    if archivos_roster:
        nombre_roster_sel = list(archivos_roster.keys())[0]
        st.session_state.ID_ROSTER_ACTIVO = archivos_roster[nombre_roster_sel]
        st.success(f"{nombre_roster_sel}")
    else: st.error("No hay Rosters.")
    
    st.markdown("---")
    st.caption("🏗️ PROYECTO / TRAMO")
    conf_prod = cargar_config_prod()
    
    if conf_prod:
        tramo_sel = st.selectbox("Seleccionar Tramo:", list(conf_prod.keys()), index=None, placeholder="Elige...")
        if tramo_sel:
            st.session_state.TRAMO_ACTIVO = tramo_sel
            st.session_state.ARCH_PROD, st.session_state.ARCH_BACKUP = conf_prod.get(tramo_sel)
            st.info(f"📁 {st.session_state.ARCH_PROD}")
    else:
        st.warning("Sin config producción")

# ==========================================
#       CARGAS DATOS AUXILIARES
# ==========================================
@st.cache_data(ttl=600)
def cargar_vehiculos_dict():
    sh = conectar_flexible(ID_VEHICULOS)
    if not sh: return {}
    try:
        return {r[0]: (r[1] if len(r)>1 else "") for r in sh.sheet1.get_all_values() if r and r[0] and "veh" not in r[0].lower()}
    except: return {}

def cargar_trabajadores(id_roster):
    if not id_roster: return []
    sh = conectar_flexible(id_roster)
    if not sh: return []
    try:
        ws = sh.sheet1 if "Roster" not in [w.title for w in sh.worksheets()] else sh.worksheet("Roster")
        datos = ws.get_all_values()
        lista = []
        col_dia = 14 
        hoy_dia = str(datetime.now().day)
        for r in range(3, 9):
            if r < len(datos) and hoy_dia in datos[r]: 
                col_dia = datos[r].index(hoy_dia)
                break
        
        for fila in datos[8:]:
            if len(fila) < 2: continue
            uid, nom = str(fila[0]).strip(), str(fila[1]).strip()
            if not uid or "id" in uid.lower(): continue
            if len(fila) > col_dia and fila[col_dia]: continue 
            tipo = "OBRA"
            if len(fila) > 2 and ("A" == str(fila[2]).upper() or "ALMACEN" in str(fila[2]).upper()): tipo = "ALMACEN"
            lista.append({"display": f"{uid} - {nom}", "tipo": tipo, "id": uid, "nombre_solo": nom})
        return lista
    except: return []

@st.cache_data(ttl=600)
def obtener_hojas_track_cached(nombre_archivo):
    sh = conectar_flexible(nombre_archivo)
    if not sh: return None
    try: return [ws.title for ws in sh.worksheets() if "HR TRACK" in ws.title.upper()]
    except: return []

# ==========================================
#       GUARDADO (CON FORMATO) Y PDF
# ==========================================
def guardar_parte(fecha, lista, vehiculo, para, id_roster):
    sh = conectar_flexible(id_roster)
    if not sh: return False
    try:
        ws = sh.sheet1 if "Roster" not in [w.title for w in sh.worksheets()] else sh.worksheet("Roster")
        header = ws.range(f"E4:AX9")
        c_idx = next((c.col for c in header if str(c.value) == str(fecha.day)), 14)
        ids_col = ws.col_values(1)
        upds = []
        for t in lista:
            try: 
                r = ids_col.index(t['ID']) + 1
                upds.append(gspread.Cell(r, c_idx, t['Turno_Letra']))
                upds.append(gspread.Cell(r, c_idx+1, t['Total_Horas']))
            except: pass
        if upds: ws.update_cells(upds)
        if para:
            try: wp = sh.worksheet("Paralizaciones")
            except: 
                wp = sh.add_worksheet("Paralizaciones", 1000, 10)
                wp.append_row(["Fecha", "Vehiculo/Lugar", "Inicio", "Fin", "Horas", "Motivo", "Usuario"])
            wp.append_row([str(fecha.date()), vehiculo, para['inicio'], para['fin'], para['duracion'], para['motivo'], st.session_state.user_name])
        return True
    except: return False

def guardar_prod_con_nota_compleja(archivo_principal, hoja, fila, col, valor, vehiculo, archivo_backup, texto_extra="", estilo_letra=None):
    """
    Guarda datos y aplica estilo si se especifica (GIROS/AISLADORES/NORMAL).
    """
    exito_principal = False
    sh = conectar_flexible(archivo_principal)
    if not sh: return False
    try:
        ws = sh.worksheet(hoja)
        ws.update_cell(fila, col, valor)
        celda_a1 = rowcol_to_a1(fila, col)
        hora_act = datetime.now().strftime("%H:%M")
        nota = f"📅 {valor} - {hora_act}\n🚛 {vehiculo}\n👷 {st.session_state.user_name}"
        if texto_extra: nota += f"\n⚠️ PENDIENTE: {texto_extra}"
        ws.insert_note(celda_a1, nota)
        
        # --- APLICAR ESTILO ---
        if estilo_letra:
            cambiar_formato_google(ws, fila, col, estilo_letra)
        else:
            cambiar_formato_google(ws, fila, col, "NORMAL")
            
        exito_principal = True
    except Exception as e:
        st.error(f"❌ Error Principal: {e}")
        return False
        
    if archivo_backup and archivo_backup != "":
        try:
            sh_bk = conectar_flexible(archivo_backup)
            if sh_bk:
                ws_bk = sh_bk.worksheet(hoja)
                ws_bk.update_cell(fila, col, valor)
                ws_bk.insert_note(rowcol_to_a1(fila, col), nota)
                if estilo_letra: cambiar_formato_google(ws_bk, fila, col, estilo_letra)
        except: pass

    cargar_datos_completos_hoja.clear() 
    return exito_principal

def generar_pdf(fecha, jefe, lista, para, prod):
    b = BytesIO()
    c = canvas.Canvas(b, pagesize=A4); _, h = A4
    start_time, end_time = "________", "________"
    if lista: start_time, end_time = lista[0]['H_Inicio'], lista[0]['H_Fin']

    y = h - 90
    c.setLineWidth(1); c.rect(40, y - 60, 515, 70) 
    c.setFont("Helvetica-Bold", 16); c.drawString(50, h - 50, "Daily Work Log - SEMI ISRAEL")
    c.setFont("Helvetica", 10); c.drawString(400, h - 50, "Israel Railways Project")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y - 15, f"Date: {fecha}"); c.drawString(250, y - 15, f"Vehicle / Location: {jefe}")
    c.drawString(50, y - 45, f"Start Time: {start_time}"); c.drawString(200, y - 45, f"End Time: {end_time}")
    c.drawString(350, y - 45, "Weather: ________")

    y_cursor = y - 80
    c.setFillColor(colors.HexColor("#2980B9")); c.rect(40, y_cursor, 515, 20, fill=1); c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8)
    headers = ["Employee Name", "ID Number", "Company", "Profession", "Normal", "Extra", "Night"]
    x_coords = [40, 180, 260, 330, 400, 450, 500, 555]
    for i, head in enumerate(headers): c.drawString(x_coords[i] + 5, y_cursor + 6, head)
    
    y_cursor -= 20; c.setFillColor(colors.black); c.setFont("Helvetica", 9); y_tabla_start = y - 80
    for t in lista:
        h_base = 8.0 if t['Total_Horas'] > 8 else t['Total_Horas']
        h_extra = t['Total_Horas'] - 8.0 if t['Total_Horas'] > 8 else 0.0
        col_base = 6 if t['Es_Noche'] else 4
        c.drawString(x_coords[0]+5, y_cursor+6, t['Nombre'][:25])
        c.drawString(x_coords[1]+5, y_cursor+6, str(t['ID']))
        c.drawString(x_coords[2]+5, y_cursor+6, "SEMI")
        c.drawString(x_coords[3]+5, y_cursor+6, "Official")
        c.drawString(x_coords[col_base]+10, y_cursor+6, f"{h_base:g}")
        if h_extra > 0: c.drawString(x_coords[5]+10, y_cursor+6, f"{h_extra:g}")
        c.setLineWidth(0.5); c.line(40, y_cursor, 555, y_cursor); y_cursor -= 20
        if y_cursor < 200: c.showPage(); y_cursor = h - 50
    
    y_min = h - 400
    while y_cursor > y_min: c.setLineWidth(0.5); c.line(40, y_cursor, 555, y_cursor); y_cursor -= 20
    c.setLineWidth(1)
    y_final = y_cursor
    for x in x_coords: c.line(x, y_tabla_start + 20, x, y_final - 0)
    c.line(555, y_tabla_start + 20, 555, y_final - 0) 

    y_bloque = y_final - 40
    if para:
        c.setStrokeColor(colors.red); c.setLineWidth(2); c.rect(40, y_bloque - 50, 515, 50)
        c.setFillColor(colors.red); c.setFont("Helvetica-Bold", 10); c.drawString(50, y_bloque - 15, "⚠️ DELAY")
        c.setFillColor(colors.black); c.setFont("Helvetica", 10); c.drawString(50, y_bloque - 35, f"{para['inicio']}-{para['fin']} | {para['motivo']}")
        c.setStrokeColor(colors.black); c.setLineWidth(1); y_bloque -= 70

    y_act = y_bloque; alt = y_act - 130
    if alt > 20:
        c.rect(40, 130, 515, alt); c.setFont("Helvetica-Bold", 10); c.drawString(50, y_act - 15, "Production:")
        yl = y_act - 35
        if prod:
            c.setFont("Helvetica", 9)
            for k,v in prod.items(): c.drawString(50, yl+5, f"- {k}: {','.join(v)}"); c.line(40, yl, 555, yl); yl-=20
        while yl > 135: c.line(40, yl, 555, yl); yl-=20

    c.setLineWidth(1); c.rect(40, 30, 515, 90); c.setFont("Helvetica-Bold", 10)
    c.drawString(50, 100, "Machinery / Materials:"); c.line(40, 70, 555, 70)
    c.drawString(50, 50, "SIGNATURE (ENCARGADO): __________________________")
    c.save(); b.seek(0); return b

def enviar_email(pdf, nombre, fecha, jefe):
    try:
        if "email" not in st.secrets: return False
        u, p, d = st.secrets["email"]["usuario"], st.secrets["email"]["password"], st.secrets["email"]["destinatario"]
        msg = MIMEMultipart(); msg['Subject']=f"Parte {fecha} {jefe}"; msg['From']=u; msg['To']=d
        att = MIMEBase('application','octet-stream'); att.set_payload(pdf.getvalue()); encoders.encode_base64(att)
        att.add_header('Content-Disposition',f"attachment; filename={nombre}"); msg.attach(att)
        s = smtplib.SMTP('smtp.gmail.com',587); s.starttls(); s.login(u,p); s.sendmail(u,d,msg.as_string()); s.quit()
        return True
    except: return False

# ==========================================
#            UI PRINCIPAL
# ==========================================
if 'lista_sel' not in st.session_state: st.session_state.lista_sel = []
if 'prod_dia' not in st.session_state: st.session_state.prod_dia = {}
if 'veh_glob' not in st.session_state: st.session_state.veh_glob = None

t1, t2 = st.tabs(["📝 Partes de Trabajo", "🏗️ Producción"])

with t1:
    st.subheader("Datos Generales")
    if st.session_state.ID_ROSTER_ACTIVO:
        c1, c2, c3, c4, c5 = st.columns([1,1,1,2,2])
        hoy = datetime.now()
        d = c1.selectbox("Día", range(1,32), index=hoy.day-1)
        m = c2.selectbox("Mes", range(1,13), index=hoy.month-1)
        a = c3.selectbox("Año", [2024,2025,2026], index=1)
        try: fecha_sel = datetime(a,m,d)
        except: fecha_sel = hoy; st.error("Fecha incorrecta")

        dv = cargar_vehiculos_dict()
        nv = [""] + list(dv.keys()) if dv else ["Error"]
        ve = c4.selectbox("Vehículo / Lugar", nv)
        st.session_state.veh_glob = ve
        c5.text_input("Detalle", value=dv.get(ve, "") if dv else "", disabled=True)
        
        st.divider()
        fl = st.radio("Filtro:", ["TODOS", "OBRA", "ALMACEN"], horizontal=True)
        trabs = cargar_trabajadores(st.session_state.ID_ROSTER_ACTIVO)
        if fl=="ALMACEN": fil = [t for t in trabs if t['tipo']=="ALMACEN"]; def_com=True
        elif fl=="OBRA": fil = [t for t in trabs if t['tipo']!="ALMACEN"]; def_com=False
        else: fil = trabs; def_com=False
            
        opc = [""] + [t['display'] for t in fil] if fil else ["Sin personal disponible"]
        trab_sel = st.selectbox("Seleccionar Operario", opc)
        
        ch1, ch2, ch3, ch4 = st.columns(4)
        h_ini = ch1.time_input("Inicio", datetime.strptime("07:00", "%H:%M").time())
        h_fin = ch2.time_input("Fin", datetime.strptime("16:00", "%H:%M").time())
        turno = ch3.selectbox("Turno", ["AUT", "D", "N"])
        comida = ch4.checkbox("-1h Comida", value=def_com)
        
        if st.button("➕ AÑADIR", type="secondary", use_container_width=True):
            if trab_sel and trab_sel != "" and trab_sel != "Sin personal disponible":
                t1 = datetime.combine(fecha_sel, h_ini); t2 = datetime.combine(fecha_sel, h_fin)
                if t2 < t1: t2 += timedelta(days=1)
                ht = (t2-t1).seconds/3600
                en, tl = False, "D"
                if turno=="N" or (turno=="AUT" and (h_ini.hour>=21 or h_ini.hour<=4)): en, tl = True, "N"
                if comida: ht = max(0, ht-1)
                pid = trab_sel.split(" - ")[0]; pnom = trab_sel.split(" - ")[1]
                st.session_state.lista_sel.append({"ID":pid, "Nombre":pnom, "Total_Horas":round(ht,2), "Turno_Letra":tl, "H_Inicio":h_ini.strftime("%H:%M"), "H_Fin":h_fin.strftime("%H:%M"), "Es_Noche":en})
        
        if st.session_state.lista_sel:
            st.markdown("### 📋 Cuadrilla del Día")
            st.dataframe(pd.DataFrame(st.session_state.lista_sel)[["ID","Nombre","Total_Horas","Turno_Letra"]], use_container_width=True)
            if st.button("Borrar Lista"): st.session_state.lista_sel=[]; st.rerun()
            
        st.markdown("---")
        para = st.checkbox("🛑 Registrar Paralización")
        d_para = None
        if para:
            cp1, cp2, cp3 = st.columns([1,1,2])
            pi = cp1.time_input("Ini Parada"); pf = cp2.time_input("Fin Parada"); pm = cp3.text_input("Motivo")
            d1, d2 = datetime.combine(hoy, pi), datetime.combine(hoy, pf)
            durp = round((d2-d1).seconds/3600, 2)
            d_para = {"inicio": str(pi), "fin": str(pf), "duracion": durp, "motivo": pm}
            
        if st.button("💾 GUARDAR TODO", type="primary", use_container_width=True):
            if not st.session_state.lista_sel: st.error("Lista vacía")
            elif not ve: st.error("Elige vehículo")
            else:
                with st.spinner("Guardando..."):
                    ok = guardar_parte(fecha_sel, st.session_state.lista_sel, ve, d_para, st.session_state.ID_ROSTER_ACTIVO)
                    pdf = generar_pdf(str(fecha_sel.date()), ve, st.session_state.lista_sel, d_para, st.session_state.prod_dia)
                    nm = f"Parte_{fecha_sel.date()}_{ve}.pdf"
                    try:
                        if "email" in st.secrets: 
                            enviar_email(pdf, nm, str(fecha_sel.date()), ve)
                            ms = "📧 Email enviado"
                        else: ms = ""
                    except: ms = "⚠️ Error Email"
                    if ok:
                        st.success(f"✅ Guardado. {ms}")
                        st.download_button("📥 PDF", pdf, nm, "application/pdf")
                        st.session_state.lista_sel=[]; st.session_state.prod_dia={}; time.sleep(3); st.rerun()

with t2:
    if not st.session_state.veh_glob: st.warning("⛔ Elige Vehículo en Pestaña 1")
    elif not st.session_state.TRAMO_ACTIVO: st.warning("⛔ Elige Tramo en menú lateral")
    else:
        nom = st.session_state.ARCH_PROD
        bk = st.session_state.ARCH_BACKUP
        hjs = obtener_hojas_track_cached(nom)
        if hjs:
            hj = st.selectbox("Hoja", hjs, index=None)
            if hj:
                with st.spinner("Cargando..."):
                    datos_completos = cargar_datos_completos_hoja(nom, hj)
                
                if datos_completos:
                    # --- PREPARACIÓN DE LISTAS ---
                    todos_los_items = datos_completos.values()
                    
                    # 1. LISTA DE PERFILES (Columna A) - ORDEN FÍSICO
                    list_perfiles_ordenada = list(datos_completos.keys())

                    # 2. Listas para filtros
                    list_cim = sorted(list(set(d['datos'][2] for d in todos_los_items if len(d['datos'])>2 and d['datos'][2])))
                    list_post = sorted(list(set(d['datos'][5] for d in todos_los_items if len(d['datos'])>5 and d['datos'][5])))
                    
                    set_anc = set()
                    for d in todos_los_items:
                        row = d['datos']
                        for idx in [17, 20, 23, 26]:
                            if len(row) > idx and row[idx]: set_anc.add(row[idx])
                    list_anc = sorted(list(set_anc))

                    # --- UI DE FILTROS ---
                    c_f1, c_f2, c_f3 = st.columns(3)
                    fil_cim = c_f1.selectbox("Filtro Cimentación", ["Todos"] + list_cim)
                    fil_post = c_f2.selectbox("Filtro Poste", ["Todos"] + list_post)
                    fil_anc = c_f3.selectbox("Filtro Anclaje", ["Todos"] + list_anc)
                    fil_km = st.text_input("Filtro Km")

                    keys_filtradas = []
                    for k, info in datos_completos.items():
                        row = info['datos']
                        if fil_km and fil_km not in str(k): continue
                        if fil_cim != "Todos":
                            val_c = row[2] if len(row)>2 else ""
                            if val_c != fil_cim: continue
                        if fil_post != "Todos":
                            val_p = row[5] if len(row)>5 else ""
                            if val_p != fil_post: continue
                        if fil_anc != "Todos":
                            vals_a = [row[i] for i in [17,20,23,26] if len(row)>i]
                            if fil_anc not in vals_a: continue
                        keys_filtradas.append(k)

                    # Selector Principal
                    it = st.selectbox("Perfil a Trabajar", keys_filtradas)
                    
                    if it:
                        # -----------------------------------------------------------
                        # CEREBRO DEL ROBOT (Lectura de Estado)
                        # -----------------------------------------------------------
                        if st.session_state.last_item_loaded != it:
                            st.session_state.last_item_loaded = it
                            info = datos_completos[it]
                            fr = info['fila_excel']
                            d = info['datos']
                            fp = safe_val(d, 8)
                            
                            estilo_detectado = "NORMAL"
                            if fp:
                                try:
                                    sh_temp = conectar_flexible(nom)
                                    ws_temp = sh_temp.worksheet(hj)
                                    estilo_detectado = detectar_estilo_celda(ws_temp, fr, 8)
                                except: pass

                            if not fp:
                                st.session_state.chk_comp = False
                                st.session_state.chk_giros = False
                                st.session_state.chk_aisl = False
                            elif estilo_detectado == "NORMAL":
                                st.session_state.chk_comp = True
                                st.session_state.chk_giros = True
                                st.session_state.chk_aisl = True
                            elif estilo_detectado == "GIROS":
                                st.session_state.chk_comp = False
                                st.session_state.chk_giros = False
                                st.session_state.chk_aisl = True
                            elif estilo_detectado == "AISLADORES":
                                st.session_state.chk_comp = False
                                st.session_state.chk_giros = True
                                st.session_state.chk_aisl = False

                        # -----------------------------------------------------------
                        # CARGA DE DATOS ACTUALES
                        # -----------------------------------------------------------
                        info = datos_completos[it]
                        fr = info['fila_excel']
                        d = info['datos']
                        nombre_poste = safe_val(d, 6) 

                        # ===========================================================
                        #       ORGANIZACIÓN DE PESTAÑAS
                        # ===========================================================
                        
                        tab_res, tab_cim, tab_pos_anc, tab_men, tab_ten = st.tabs(["📊 Resumen", "🧱 Cimentación", "🗼 Postes y Anclajes", "🔧 Ménsulas", "⚡ Tendidos"])

                        # --- PESTAÑA 0: RESUMEN ---
                        with tab_res:
                            st.markdown(f"### 📋 Perfil: {it} (Poste {nombre_poste})")
                            st.markdown("---")
                            f_cim_res = safe_val(d, 5)
                            f_pos_res = safe_val(d, 8)
                            f_men_res = safe_val(d, 38)
                            f_ten_res = safe_val(d, 39) 
                            
                            cols_t_res, cols_f_res = [18, 21, 24, 27], [20, 23, 26, 29]
                            tiene_anclajes = False; anclajes_completos = True
                            for i in range(4):
                                if safe_val(d, cols_t_res[i]):
                                    tiene_anclajes = True
                                    if not safe_val(d, cols_f_res[i]): anclajes_completos = False

                            cr1, cr2 = st.columns(2)
                            with cr1:
                                if f_cim_res: st.success(f"🧱 **Cim:** ✅ {f_cim_res}")
                                else: st.error("🧱 **Cim:** ❌")
                                
                                if f_pos_res: 
                                    if not st.session_state.chk_giros or not st.session_state.chk_aisl:
                                        st.warning(f"🗼 **Poste:** ⚠️ {f_pos_res} (Pendientes)")
                                    else: st.success(f"🗼 **Poste:** ✅ {f_pos_res}")
                                else: st.error("🗼 **Poste:** ❌")

                            with cr2:
                                if not tiene_anclajes: st.info("⚓ **Anc:** ➖")
                                elif anclajes_completos: st.success("⚓ **Anc:** ✅")
                                else: st.error("⚓ **Anc:** ❌")
                                    
                                if f_men_res: st.success(f"🔧 **Mén:** ✅ {f_men_res}")
                                else: st.error("🔧 **Mén:** ❌")

                            st.markdown("---")
                            if f_ten_res: st.success(f"⚡ **Cable LA-280:** ✅ {f_ten_res}")
                            else: st.warning("⚡ **Cable LA-280:** ❌ Pendiente")

                        # --- PESTAÑA 1: CIMENTACIÓN ---
                        with tab_cim:
                            st.subheader("Fase de Obra Civil")
                            c1, c2 = st.columns([1, 2])
                            ec, fc = safe_val(d, 3), safe_val(d, 5)
                            c1.info(f"Tipo: {ec}")
                            if fc: c2.success(f"✅ Ejecutado el: {fc}")
                            elif c2.button("Grabar CIMENTACIÓN", use_container_width=True):
                                guardar_prod_con_nota_compleja(nom, hj, fr, 5, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, bk)
                                if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                                st.session_state.prod_dia[it].append("CIM"); st.rerun()

                        # --- PESTAÑA 2: POSTES Y ANCLAJES ---
                        with tab_pos_anc:
                            st.subheader("1. Estructura (Poste)")
                            c1, c2 = st.columns([1, 2])
                            ep, fp = safe_val(d, 6), safe_val(d, 8)
                            c1.info(f"Tipo: {ep}")
                            if st.session_state.chk_comp and fp:
                                c2.success(f"✅ TERMINADO: {fp}")
                            else:
                                with c2:
                                    cc1, cc2, cc3 = st.columns(3)
                                    st.session_state.chk_giros = cc1.checkbox("Giros", value=st.session_state.chk_giros)
                                    st.session_state.chk_aisl = cc2.checkbox("Aisladores", value=st.session_state.chk_aisl)
                                    st.session_state.chk_comp = cc3.checkbox("Completo", value=st.session_state.chk_comp, on_change=on_completo_change)
                                    if st.button("💾 Grabar POSTE", use_container_width=True):
                                        txt = ""; estilo = "NORMAL"
                                        if not st.session_state.chk_giros: txt += "GIROS FALTAN. "; estilo = "GIROS"
                                        if not st.session_state.chk_aisl: txt += "AISLADORES FALTAN. "; estilo = "AISLADORES"
                                        if st.session_state.chk_comp: estilo = "NORMAL"
                                        guardar_prod_con_nota_compleja(nom, hj, fr, 8, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, bk, txt, estilo_letra=estilo)
                                        if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                                        st.session_state.prod_dia[it].append("POSTE"); st.rerun()
                            
                            st.divider(); st.subheader("2. Anclajes")
                            cols_t, cols_f = [18, 21, 24, 27], [20, 23, 26, 29]
                            typs, cols_escritura, done = [], [], False
                            for i in range(4):
                                v = safe_val(d, cols_t[i])
                                if v:
                                    typs.append(str(v)); cols_escritura.append(cols_f[i])
                                    if safe_val(d, cols_f[i]): done = True
                            c1, c2 = st.columns([1, 2])
                            c1.info(f"Tipos: {', '.join(typs) if typs else 'Ninguno'}")
                            if not typs: c2.write("-")
                            elif done: c2.success("✅ Completos")
                            elif c2.button("Grabar ANCLAJES", use_container_width=True):
                                hoy = datetime.now().strftime("%d/%m/%Y")
                                for c_idx in cols_escritura:
                                    guardar_prod_con_nota_compleja(nom, hj, fr, c_idx, hoy, st.session_state.veh_glob, bk)
                                if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                                st.session_state.prod_dia[it].append("ANC"); st.rerun()

                        # --- PESTAÑA 3: MÉNSULAS ---
                        with tab_men:
                            st.subheader("Equipamiento: Ménsulas")
                            c1, c2 = st.columns([1, 2])
                            m_desc = f"{safe_val(d,32) or ''} {safe_val(d,33) or ''}".strip()
                            fm = safe_val(d, 38)
                            c1.info(f"Tipo: {m_desc or '-'}")
                            if fm: c2.success(f"Hecho: {fm}")
                            elif c2.button("Grabar MÉNSULA", use_container_width=True):
                                guardar_prod_con_nota_compleja(nom, hj, fr, 38, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, bk)
                                if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                                st.session_state.prod_dia[it].append("MEN"); st.rerun()

                        # --- PESTAÑA 4: TENDIDOS (LA-280) ---
                        with tab_ten:
                            st.subheader("⚡ Tendido Cable LA-280")
                            st.caption("Actualización por Tramos (Selecciona Perfiles)")
                            
                            f_la280 = safe_val(d, 39)
                            
                            c1, c2 = st.columns([1,2])
                            if f_la280:
                                c1.success(f"Estado en {it}:")
                                c2.success(f"✅ {f_la280}")
                            else:
                                c1.warning(f"Estado en {it}:")
                                c2.error("❌ PENDIENTE")
                            
                            st.divider()
                            st.write("### 🛤️ Registrar Tramo")
                            
                            # 1. Selectores usando la LISTA REAL ORDENADA (Columna A)
                            idx_def = 0
                            if it in list_perfiles_ordenada:
                                idx_def = list_perfiles_ordenada.index(it)
                            
                            col_sel1, col_sel2 = st.columns(2)
                            p_ini = col_sel1.selectbox("Desde Perfil:", list_perfiles_ordenada, index=idx_def)
                            p_fin = col_sel2.selectbox("Hasta Perfil:", list_perfiles_ordenada, index=idx_def)
                            
                            fecha_tendido = datetime.now().strftime("%d/%m/%Y")
                            
                            if st.button("🚀 GRABAR TRAMO LA-280", type="primary", use_container_width=True):
                                try:
                                    idx_a = list_perfiles_ordenada.index(p_ini)
                                    idx_b = list_perfiles_ordenada.index(p_fin)
                                    
                                    if idx_a > idx_b: idx_a, idx_b = idx_b, idx_a
                                    
                                    # Sublista de perfiles
                                    perfiles_a_actualizar = list_perfiles_ordenada[idx_a : idx_b + 1]
                                    
                                    st.write(f"⏳ Grabando en {len(perfiles_a_actualizar)} perfiles (Columna AM)...")
                                    barra = st.progress(0)
                                    
                                    contador = 0
                                    for perfil_id in perfiles_a_actualizar:
                                        if perfil_id in datos_completos:
                                            fila_real = datos_completos[perfil_id]['fila_excel']
                                            
                                            guardar_prod_con_nota_compleja(
                                                nom, hj, fila_real, 39, 
                                                fecha_tendido, st.session_state.veh_glob, bk, 
                                                texto_extra=f"Tendido LA-280 [{p_ini} -> {p_fin}]"
                                            )
                                            contador += 1
                                            barra.progress(int((contador / len(perfiles_a_actualizar)) * 100))
                                    
                                    st.success(f"✅ ¡Hecho! {contador} perfiles actualizados.")
                                    time.sleep(2)
                                    
                                    if it not in st.session_state.prod_dia: st.session_state.prod_dia[it]=[]
                                    st.session_state.prod_dia[it].append(f"Tendido LA-280 ({p_ini}-{p_fin})")
                                    st.rerun()
                                    
                                except Exception as e:
                                    st.error(f"Error en el proceso: {e}")
