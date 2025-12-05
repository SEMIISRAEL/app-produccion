import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
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
from email.mime.text import MIMEText
from email import encoders
from gspread.utils import rowcol_to_a1

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Gestor SEMI - Tablet", layout="wide", page_icon="🏗️")

# --- IDs FIJOS ---
ID_ROSTER = "1ezFvpyTzkL98DJjpXeeGuqbMy_kTZItUC9FDkxFlD08"
ID_VEHICULOS = "19PWpeCz8pl5NEDpK-omX5AdrLuJgOPrn6uSjtUGomY8"
ID_CONFIG_PROD = "1uCu5pq6l1CjqXKPEkGkN-G5Z5K00qiV9kR_bGOii6FU"

# ==========================================
#           LOGIN / CONEXIÓN
# ==========================================
def check_login():
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.user_role = None
        st.session_state.user_name = None

    if not st.session_state.logged_in:
        st.markdown("<h1 style='text-align: center;'>🔐 Acceso Restringido</h1>", unsafe_allow_html=True)
        c1, c2, c3 = st.columns([1, 2, 1])
        with c2:
            usuario = st.text_input("Usuario")
            password = st.text_input("Contraseña", type="password")
            if st.button("Entrar", type="primary", use_container_width=True):
                try:
                    users_db = st.secrets["usuarios"]
                    roles_db = st.secrets["roles"]
                    if usuario in users_db and users_db[usuario] == password:
                        st.session_state.logged_in = True
                        st.session_state.user_name = usuario
                        st.session_state.user_role = roles_db.get(usuario, "invitado")
                        st.rerun()
                    else: st.error("❌ Incorrecto")
                except: st.error("⚠️ Error Secrets")
        return False
    return True

if not check_login(): st.stop()

# ==========================================
#      CARGA CONFIGURACIÓN (LEE COL C)
# ==========================================
@st.cache_data(ttl=600)
def cargar_config_prod():
    """Lee el archivo config y devuelve {Tramo: (Archivo Principal, Archivo Backup)}"""
    sh = conectar_flexible(ID_CONFIG_PROD)
    if not sh: return {}
    try:
        # Leemos Columna A, B, y C
        datos = sh.sheet1.get_all_values()
        config = {}
        for row in datos:
            if len(row) >= 3 and row[0] and row[1]: # Asegura que haya 3 columnas
                tramo = row[0].strip()
                archivo_principal = row[1].strip()
                archivo_backup = row[2].strip() if len(row) > 2 else "" # Columna C (índice 2)
                
                if tramo and archivo_principal:
                    config[tramo] = (archivo_principal, archivo_backup)
            # Solo saltamos la primera fila si es cabecera
            elif row and row[0].lower() in ["tramo", "seccion"]: continue 
        return config
    except Exception as e: 
        st.warning(f"Error cargando config prod: {e}")
        return {}

# ==========================================
#      GUARDADO DUAL (CON FALLBACK)
# ==========================================

def guardar_prod_con_nota(archivo_principal, hoja, fila, col, valor, vehiculo, archivo_backup=None):
    """Guarda en el archivo principal (obligatorio) y en el backup (best effort)"""
    
    exito_principal = False
    
    # 1. ESCRITURA EN ARCHIVO PRINCIPAL (MANDATORIO)
    sh = conectar_flexible(archivo_principal)
    if not sh: 
        st.error(f"❌ No se encontró el archivo principal: {archivo_principal}")
        return False
    
    try:
        ws = sh.worksheet(hoja)
        ws.update_cell(fila, col, valor)
        
        # Añadir NOTA
        celda_a1 = rowcol_to_a1(fila, col)
        hora_act = datetime.now().strftime("%H:%M")
        nota = f"📅 {valor} - {hora_act}\n🚛 {vehiculo}\n👷 {st.session_state.user_name}"
        ws.insert_note(celda_a1, nota)
        
        exito_principal = True
        
    except Exception as e:
        st.error(f"❌ Error al escribir en hoja principal: {e}")
        return False
        
    # 2. ESCRITURA EN BACKUP (OPCIONAL/BEST EFFORT)
    if archivo_backup and exito_principal:
        try:
            sh_bk = conectar_flexible(archivo_backup)
            if sh_bk:
                ws_bk = sh_bk.worksheet(hoja)
                ws_bk.update_cell(fila, col, valor)
                ws_bk.insert_note(rowcol_to_a1(fila, col), nota)
        except Exception as e:
            st.warning(f"⚠️ Atención: Falló la copia de seguridad en {archivo_backup} ({e})")

    # Limpiamos caché para que el usuario vea el estado actual del archivo
    cargar_datos_completos_hoja.clear() 
    return True

# ==========================================
#      RESTO DE FUNCIONES (UNTOUCHED)
# ==========================================
# ... (Funciones auxiliares, guardado de horas, PDF, etc.)

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
            except: wp = sh.add_worksheet("Paralizaciones", 1000, 10)
            wp.append_row([str(fecha.date()), vehiculo, para['inicio'], para['fin'], para['duracion'], para['motivo'], st.session_state.user_name])
        return True
    except: return False

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
    c.drawString(50, y - 15, f"Date: {fecha}"); c.drawString(250, y - 15, f"Vehicle / Activity: {jefe}")
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
    for x in x_coords: c.line(x, y_tabla_start + 20, x, y_final - 20)
    c.line(555, y_tabla_start + 20, 555, y_final - 20) 

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
#           UI
# ==========================================
if 'lista_sel' not in st.session_state: st.session_state.lista_sel = []
if 'prod_dia' not in st.session_state: st.session_state.prod_dia = {}
if 'veh_glob' not in st.session_state: st.session_state.veh_glob = None

t1, t2 = st.tabs(["📝 Partes de Trabajo", "🏗️ Producción"])

with t1:
    st.subheader("Datos Generales")
    if ID_ROSTER_ACTIVO:
        c1, c2, c3, c4, c5 = st.columns([1,1,1,2,2])
        d = c1.selectbox("Día", range(1,32), index=datetime.now().day-1)
        m = c2.selectbox("Mes", range(1,13), index=datetime.now().month-1)
        a = c3.selectbox("Año", [2024,2025,2026], index=1)
        try: fecha_sel = datetime(a,m,d)
        except: fecha_sel = hoy; st.error("Fecha incorrecta")

        dv = cargar_vehiculos_dict()
        nv = [""] + list(dv.keys()) if dv else ["Error"]
        ve = c4.selectbox("Vehículo / Lugar", nv)
        st.session_state.veh_glob = ve
        c5.text_input("Detalle", value=dv.get(ve, "") if dv else "", disabled=True)
        
        st.divider()
        fl = st.radio("Filtro", ["TODOS", "OBRA", "ALMACEN"], horizontal=True)
        trabs = cargar_trabajadores(ID_ROSTER_ACTIVO)
        if fl=="ALMACEN": fil=[t for t in trabs if t['tipo']=="ALMACEN"]; def_com=True
        elif fl=="OBRA": fil=[t for t in trabs if t['tipo']!="ALMACEN"]; def_com=False
        else: fil=trabs; def_com=False
            
        opc = [""] + [t['display'] for t in fil] if fil else ["Sin personal disponible"]
        trab_sel = st.selectbox("Seleccionar Operario", opc)
        
        ch1, ch2, ch3, ch4 = st.columns(4)
        h_ini = ch1.time_input("Inicio", datetime.strptime("07:00", "%H:%M").time())
        h_fin = ch2.time_input("Fin", datetime.strptime("16:00", "%H:%M").time())
        turno = ch3.selectbox("Turno", ["AUT", "D", "N"])
        comida = ch4.checkbox("-1h Comida", value=def_com)
        
        if st.button("➕ AÑADIR A LA LISTA", type="secondary", use_container_width=True):
            if trab_sel and trab_sel not in ["", "Sin personal disponible"]:
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
            
        st.divider()
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
                    # GUARDADO EN ROSTER PRINCIPAL
                    ok = guardar_parte(fecha_sel, st.session_state.lista_sel, ve, d_para, ID_ROSTER_ACTIVO)
                    
                    pdf = generar_pdf(str(fecha_sel.date()), ve, st.session_state.lista_sel, d_para, st.session_state.prod)
                    nm = f"Parte_{fecha_sel.date()}_{ve}.pdf"
                    
                    try:
                        if "email" in st.secrets: 
                            enviado = enviar_email(pdf, nm, str(fecha_sel.date()), ve)
                            ms = "📧 Email enviado"
                        else: ms = ""
                    except: ms = "⚠️ Error Email"
                    
                    if ok:
                        st.success(f"✅ Guardado. {ms}")
                        st.download_button("📥 PDF", pdf, nm, "application/pdf")
                        st.session_state.lista_sel=[]; st.session_state.prod={}; time.sleep(3); st.rerun()

with t2:
    if not st.session_state.veh_glob: st.warning("⛔ Elige Vehículo en Pestaña 1")
    elif not TRAMO_ACTIVO: st.warning("⛔ Elige Tramo en menú lateral")
    else:
        conf = cargar_config_prod()
        nom = conf.get(TRAMO_ACTIVO)
        
        # OBTENEMOS LOS ARCHIVOS
        if isinstance(nom, tuple):
            nom_arch_principal, nom_arch_backup = nom
        else:
            nom_arch_principal, nom_arch_backup = nom, None
        
        hjs = obtener_hojas_track_cached(nom_arch_principal)
        
        if hjs:
            hj = st.selectbox("Hoja", hjs, index=None)
            if hj:
                with st.spinner("Cargando..."):
                    datos_completos = cargar_datos_completos_hoja(nom_arch_principal, hj)
                
                if datos_completos:
                    fil = st.text_input("Filtro Km")
                    keys = list(datos_completos.keys())
                    if fil: keys = [k for k in keys if fil in str(k)]
                    it = st.selectbox("Elemento", keys)
                    
                    if it:
                        info = datos_completos[it]
                        fr = info['fila_excel']
                        d = info['datos']
                        
                        st.divider()
                        st.markdown(f"### 📍 {it}")
                        
                        # CIMENTACIÓN
                        c1, c2 = st.columns([1, 2])
                        ec, fc = safe_val(d, 3), safe_val(d, 5)
                        c1.info(f"Cim: {ec or '-'}")
                        if fc: c2.success(f"Hecho: {fc}")
                        elif c2.button("Grabar CIM", key="b_cim"):
                            guardar_prod_con_nota(nom_arch_principal, hj, fr, 5, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, nom_arch_backup)
                            if it not in st.session_state.prod: st.session_state.prod[it]=[]
                            st.session_state.prod[it].append("CIM"); st.rerun()
                            
                        st.divider()
                        
                        # POSTE
                        c1, c2 = st.columns([1, 2])
                        ep, fp = safe_val(d, 6), safe_val(d, 8)
                        c1.info(f"Poste: {ep or '-'}")
                        if fp: c2.success(f"Hecho: {fp}")
                        elif c2.button("Grabar POSTE", key="b_pos"):
                            guardar_prod_con_nota(nom_arch_principal, hj, fr, 8, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, nom_arch_backup)
                            if it not in st.session_state.prod: st.session_state.prod[it]=[]
                            st.session_state.prod[it].append("POSTE"); st.rerun()
                            
                        st.divider()
                        
                        # MENSULA
                        c1, c2 = st.columns([1, 2])
                        m = f"{safe_val(d,33) or ''} {safe_val(d,34).value or ''}".strip()
                        fm = safe_val(d, 38)
                        c1.info(f"Ménsula: {m or '-'}")
                        if fm: c2.success(f"Hecho: {fm}")
                        elif c2.button("Grabar MENSULA", key="b_men"):
                            guardar_prod_con_nota(nom_arch_principal, hj, fr, 38, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, nom_arch_backup)
                            if it not in st.session_state.prod: st.session_state.prod[it]=[]
                            st.session_state.prod[it].append("MEN"); st.rerun()
                        
                        st.divider()
                        
                        # ANCLAJES
                        if st.button("Grabar Anclajes", key="b_anc"):
                            # Usamos el principal como referencia para obtener las columnas de fecha
                            sh_ref = conectar_flexible(nom_arch_principal)
                            if sh_ref:
                                ws_ref = sh_ref.worksheet(hj)
                                # Lógica para determinar qué columnas escribir
                                idx_t = [18, 21, 24, 27]; idx_f = [20, 23, 26, 29]
                                cols_fecha = []
                                for i in range(4):
                                    # Si hay un valor en la columna de tipo (Col R, U, X, AA)
                                    if ws_ref.cell(fr, idx_t[i]).value:
                                        cols_fecha.append(idx_f[i]) # Columna donde escribir la fecha (T, W, Z, AC)
                                
                                if cols_fecha:
                                    hoy = datetime.now().strftime("%d/%m/%Y")
                                    # Escribimos en todas las columnas activas (mismo dato, diferente celda)
                                    for c in cols_fecha:
                                        guardar_prod_con_nota(nom_arch_principal, hj, fr, c, hoy, st.session_state.veh_glob, ARCHIVO_PROD_BACKUP_ACTIVO)
                                    
                                    if it not in st.session_state.prod: st.session_state.prod[it]=[]
                                    st.session_state.prod[it].append("ANC"); st.rerun()
                                else:
                                    st.warning("No hay anclajes definidos para este poste.")
