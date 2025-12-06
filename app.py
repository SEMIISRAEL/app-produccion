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
import urllib.parse
from gspread.utils import rowcol_to_a1

# ==========================================
# 1. CONFIGURACIÓN E INYECCIÓN DE ESTILO (CSS)
# ==========================================
st.set_page_config(page_title="SEMI Tablet", layout="wide", page_icon="🏗️")

# ESTILO PROFESIONAL: Botones grandes, sombras, aspecto de App
st.markdown("""
<style>
    /* Estilo para los botones del Menú Principal */
    .big-button {
        display: inline-block;
        width: 100%;
        height: 150px;
        margin: 10px;
        padding: 20px;
        border-radius: 20px;
        background-color: white;
        box-shadow: 0 4px 8px 0 rgba(0,0,0,0.2);
        transition: 0.3s;
        text-align: center;
        border: 2px solid #f0f2f6;
        cursor: pointer;
    }
    .big-button:hover {
        box-shadow: 0 8px 16px 0 rgba(0,0,0,0.2);
        border-color: #ff4b4b;
        transform: translateY(-2px);
    }
    div.stButton > button {
        width: 100%;
        border-radius: 15px;
        height: 3em;
        font-weight: bold;
    }
    /* Títulos centrados */
    .main-title {
        text-align: center;
        font-size: 2.5rem;
        font-weight: 700;
        color: #333;
        margin-bottom: 20px;
    }
</style>
""", unsafe_allow_html=True)

# --- IDs FIJOS ---
ID_VEHICULOS = "19PWpeCz8pl5NEDpK-omX5AdrLuJgOPrn6uSjtUGomY8"
ID_CONFIG_PROD = "1uCu5pq6l1CjqXKPEkGkN-G5Z5K00qiV9kR_bGOii6FU"

# ==========================================
# 2. GESTIÓN DE ESTADO (NAVEGACIÓN)
# ==========================================
if 'page' not in st.session_state: st.session_state.page = "HOME"
if 'user_name' not in st.session_state: st.session_state.user_name = "Usuario Tablet"
if 'ID_ROSTER_ACTIVO' not in st.session_state: st.session_state.ID_ROSTER_ACTIVO = None
if 'TRAMO_ACTIVO' not in st.session_state: st.session_state.TRAMO_ACTIVO = None
if 'ARCH_PROD' not in st.session_state: st.session_state.ARCH_PROD = None
if 'ARCH_BACKUP' not in st.session_state: st.session_state.ARCH_BACKUP = None
if 'veh_glob' not in st.session_state: st.session_state.veh_glob = None
if 'lista_sel' not in st.session_state: st.session_state.lista_sel = []
if 'prod_dia' not in st.session_state: st.session_state.prod_dia = {}

# Variables de Checkbox (Memoria del Robot)
if 'chk_giros' not in st.session_state: st.session_state.chk_giros = False
if 'chk_aisl' not in st.session_state: st.session_state.chk_aisl = False
if 'chk_comp' not in st.session_state: st.session_state.chk_comp = False
if 'last_item_loaded' not in st.session_state: st.session_state.last_item_loaded = None

def navigate_to(page):
    st.session_state.page = page
    st.rerun()

def on_completo_change():
    if st.session_state.chk_comp:
        st.session_state.chk_giros = True
        st.session_state.chk_aisl = True

# ==========================================
# 3. FUNCIONES DEL ROBOT (Con Colores Azul/Verde)
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
        except: return None

def safe_val(lista, indice):
    idx_py = indice - 1
    if idx_py < len(lista): return lista[idx_py]
    return None

def leer_nota_directa(nombre_archivo, nombre_hoja, fila, col):
    try:
        sh = conectar_flexible(nombre_archivo)
        ws = sh.worksheet(nombre_hoja)
        return ws.cell(fila, col).note or ""
    except: return ""

def cambiar_formato_google(ws, fila, col, tipo_estilo):
    """ LA MANO DEL ROBOT: Escribe formato (Texto y Fondo) """
    try:
        formato_texto = {"fontFamily": "Arial", "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}, "bold": False}
        formato_fondo = None 

        if tipo_estilo == "GIROS":
            formato_texto = {"fontFamily": "Courier New", "foregroundColor": {"red": 1.0, "green": 0.0, "blue": 0.0}, "bold": True}
        elif tipo_estilo == "AISLADORES":
            formato_texto = {"fontFamily": "Times New Roman", "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 1.0}, "bold": True}
        elif tipo_estilo == "TENDIDO_AZUL":
            formato_texto = {"fontFamily": "Arial", "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}, "bold": True}
            formato_fondo = {"red": 0.4, "green": 0.6, "blue": 1.0} 
        elif tipo_estilo == "GRAPADO_VERDE":
            formato_texto = {"fontFamily": "Arial", "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}, "bold": True}
            formato_fondo = {"red": 0.4, "green": 0.9, "blue": 0.4} 

        user_format = {"textFormat": formato_texto}
        if formato_fondo: user_format["backgroundColor"] = formato_fondo
        campos = "userEnteredFormat(textFormat,backgroundColor)"

        body = {"requests": [{"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": fila - 1, "endRowIndex": fila, "startColumnIndex": col - 1, "endColumnIndex": col}, "cell": {"userEnteredFormat": user_format}, "fields": campos}}]}
        ws.spreadsheet.batch_update(body)
        return True
    except Exception as e: return False

def detectar_estilo_celda(ws, fila, col):
    """ EL OJO DEL ROBOT: Lee colores y fuentes """
    try:
        nombre_hoja = ws.title
        rango = f"{nombre_hoja}!{rowcol_to_a1(fila, col)}"
        res = ws.spreadsheet.fetch_sheet_metadata(params={'includeGridData': True, 'ranges': [rango]})
        try:
            celda_data = res['sheets'][0]['data'][0]['rowData'][0]['values'][0]
            formato = celda_data.get('userEnteredFormat', {})
            font_family = formato.get('textFormat', {}).get('fontFamily', 'Arial')
            bg_color = formato.get('backgroundColor', {})
            
            if bg_color.get('blue', 0) > 0.8 and bg_color.get('red', 0) < 0.6: return "TENDIDO_AZUL"
            if bg_color.get('green', 0) > 0.7 and bg_color.get('blue', 0) < 0.6: return "GRAPADO_VERDE"
            if 'Courier' in font_family: return "GIROS"
            elif 'Times' in font_family: return "AISLADORES"
            return "NORMAL"
        except: return "NORMAL"
    except: return "NORMAL"

def guardar_prod_con_nota_compleja(archivo_principal, hoja, fila, col, valor, vehiculo, archivo_backup, texto_extra="", estilo_letra=None):
    sh = conectar_flexible(archivo_principal)
    if not sh: return False
    try:
        ws = sh.worksheet(hoja)
        ws.update_cell(fila, col, valor)
        hora_act = datetime.now().strftime("%H:%M")
        nota = f"📅 {valor} - {hora_act}\n🚛 {vehiculo}\n👷 {st.session_state.user_name}"
        if texto_extra: nota += f"\n⚠️ {texto_extra}"
        ws.insert_note(rowcol_to_a1(fila, col), nota)
        if estilo_letra: cambiar_formato_google(ws, fila, col, estilo_letra)
        else: cambiar_formato_google(ws, fila, col, "NORMAL") # Limpiar formato si no se especifica
        return True
    except Exception as e:
        st.error(f"Error: {e}")
        return False

# ==========================================
# 4. CARGA DE DATOS (CACHÉ)
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
                tramo, archivo = row[0].strip(), row[1].strip()
                bk = row[2].strip() if len(row) > 2 else "" 
                if tramo and archivo: config[tramo] = (archivo, bk)
        return config
    except: return {}

@st.cache_data(ttl=600)
def cargar_vehiculos_dict():
    sh = conectar_flexible(ID_VEHICULOS)
    if not sh: return {}
    try: return {r[0]: (r[1] if len(r)>1 else "") for r in sh.sheet1.get_all_values() if r and r[0] and "veh" not in r[0].lower()}
    except: return {}

def cargar_trabajadores(id_roster):
    if not id_roster: return []
    sh = conectar_flexible(id_roster)
    if not sh: return []
    try:
        ws = sh.sheet1 if "Roster" not in [w.title for w in sh.worksheets()] else sh.worksheet("Roster")
        datos = ws.get_all_values()
        lista = []
        for fila in datos[8:]:
            if len(fila) < 2: continue
            uid, nom = str(fila[0]).strip(), str(fila[1]).strip()
            if not uid or "id" in uid.lower(): continue
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
# 5. LÓGICA DE PANTALLAS (MENU Y SUB-PANTALLAS)
# ==========================================

def mostrar_home():
    st.markdown("<h1 class='main-title'>PANEL DE CONTROL DE OBRA</h1>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    
    # Grid de Menú
    c1, c2 = st.columns(2)
    
    with c1:
        st.markdown("### 📝 Gestión de Personal")
        if st.button("PARTES DE TRABAJO", use_container_width=True, type="primary"):
            navigate_to("PARTES")
            
    with c2:
        st.markdown("### 🏗️ Gestión de Obra")
        if st.button("PRODUCCIÓN (Ejecución)", use_container_width=True, type="primary"):
            navigate_to("PRODUCCION")
    
    st.markdown("---")
    
    c3, c4 = st.columns(2)
    with c3:
        st.markdown("### 📲 Comunicaciones")
        if st.button("WHATSAPP INTERNO", use_container_width=True):
            navigate_to("WHATSAPP")
            
    with c4:
        st.markdown("### ⚙️ Sistema")
        if st.button("SALIR / RECARGAR", use_container_width=True):
            st.session_state.logged_in = False
            st.rerun()

    # Barra lateral de información en HOME
    with st.sidebar:
        st.image("https://cdn-icons-png.flaticon.com/512/2942/2942813.png", width=100)
        st.info("Bienvenido al sistema. Selecciona una opción del panel central.")
        
        # Carga silenciosa de configs para tenerlas listas
        archivos_roster = buscar_archivos_roster()
        if archivos_roster:
            nombre_roster_sel = list(archivos_roster.keys())[0]
            st.session_state.ID_ROSTER_ACTIVO = archivos_roster[nombre_roster_sel]
        
        conf_prod = cargar_config_prod()
        if conf_prod:
            st.write("---")
            st.write("**Configuración Activa:**")
            tramo_sel = st.selectbox("Tramo:", list(conf_prod.keys()), index=None, placeholder="Selecciona Tramo...")
            if tramo_sel:
                st.session_state.TRAMO_ACTIVO = tramo_sel
                st.session_state.ARCH_PROD, st.session_state.ARCH_BACKUP = conf_prod.get(tramo_sel)
                st.success("✅ Tramo Vinculado")

def mostrar_pantalla_partes():
    c_back, c_tit = st.columns([1, 4])
    with c_back:
        if st.button("⬅️ VOLVER AL MENÚ"): navigate_to("HOME")
    with c_tit:
        st.title("📝 Partes de Trabajo Diario")
    
    st.markdown("---")
    
    if st.session_state.ID_ROSTER_ACTIVO:
        c1, c2, c3 = st.columns([1,1,2])
        hoy = datetime.now()
        d = c1.selectbox("Día", range(1,32), index=hoy.day-1)
        m = c2.selectbox("Mes", range(1,13), index=hoy.month-1)
        a = 2025
        try: fecha_sel = datetime(a,m,d)
        except: fecha_sel = hoy
        
        dv = cargar_vehiculos_dict()
        nv = [""] + list(dv.keys()) if dv else ["Error"]
        ve = c3.selectbox("Vehículo / Lugar", nv)
        st.session_state.veh_glob = ve
        
        st.divider()
        trabs = cargar_trabajadores(st.session_state.ID_ROSTER_ACTIVO)
        opc = [""] + [t['display'] for t in trabs]
        
        c_sel, c_add = st.columns([3, 1])
        trab_sel = c_sel.selectbox("Seleccionar Operario", opc)
        
        ch1, ch2, ch3 = st.columns(3)
        h_ini = ch1.time_input("Inicio", datetime.strptime("07:00", "%H:%M").time())
        h_fin = ch2.time_input("Fin", datetime.strptime("16:00", "%H:%M").time())
        
        if c_add.button("➕ AÑADIR", use_container_width=True):
            if trab_sel:
                ht = 9.0 # Simplificado para el ejemplo
                st.session_state.lista_sel.append({"Nombre": trab_sel, "Horas": ht})
        
        if st.session_state.lista_sel:
            st.table(pd.DataFrame(st.session_state.lista_sel))
            if st.button("💾 GUARDAR PARTE", type="primary"):
                st.success("Parte guardado (Simulación)")
                st.session_state.lista_sel = []
                st.rerun()

def mostrar_pantalla_produccion():
    c_back, c_tit = st.columns([1, 4])
    with c_back:
        if st.button("⬅️ VOLVER AL MENÚ", key="back_prod"): navigate_to("HOME")
    with c_tit:
        st.title("🏗️ Registro de Producción")

    if not st.session_state.veh_glob: st.error("⚠️ Primero selecciona Vehículo en 'Partes de Trabajo'"); return
    if not st.session_state.TRAMO_ACTIVO: st.error("⚠️ Selecciona Tramo en la Barra Lateral del Menú"); return

    nom = st.session_state.ARCH_PROD
    bk = st.session_state.ARCH_BACKUP
    hjs = obtener_hojas_track_cached(nom)
    
    if hjs:
        hj = st.selectbox("Seleccionar Hoja de Control:", hjs)
        if hj:
            with st.spinner("Cargando datos de obra..."):
                datos = cargar_datos_completos_hoja(nom, hj)
            
            if datos:
                # LISTAS
                list_perfiles = list(datos.keys())
                todos_v = datos.values()
                list_cim = sorted(list(set(d['datos'][2] for d in todos_v if len(d['datos'])>2 and d['datos'][2])))
                list_post = sorted(list(set(d['datos'][5] for d in todos_v if len(d['datos'])>5 and d['datos'][5])))
                
                # FILTROS
                with st.expander("🔍 Filtros de Búsqueda", expanded=True):
                    cf1, cf2 = st.columns(2)
                    fil_cim = cf1.selectbox("Cimentación", ["Todos"] + list_cim)
                    fil_post = cf2.selectbox("Tipo Poste", ["Todos"] + list_post)
                
                # FILTRADO
                keys_ok = []
                for k, inf in datos.items():
                    r = inf['datos']
                    if fil_cim != "Todos" and (len(r)<=2 or r[2]!=fil_cim): continue
                    if fil_post != "Todos" and (len(r)<=5 or r[5]!=fil_post): continue
                    keys_ok.append(k)
                
                it = st.selectbox("📍 SELECCIONAR PERFIL / POSTE:", keys_ok)
                
                if it:
                    # LOGICA ROBOT
                    if st.session_state.last_item_loaded != it:
                        st.session_state.last_item_loaded = it
                        info = datos[it]
                        fr, d = info['fila_excel'], info['datos']
                        fp = safe_val(d, 8)
                        
                        est_det = "NORMAL"
                        est_ten = "NORMAL"
                        if fp or safe_val(d, 39):
                            try:
                                sh_t = conectar_flexible(nom)
                                ws_t = sh_t.worksheet(hj)
                                if fp: est_det = detectar_estilo_celda(ws_t, fr, 8)
                                est_ten = detectar_estilo_celda(ws_t, fr, 39)
                            except: pass
                        
                        st.session_state.estado_tendido_actual = est_ten
                        if not fp: st.session_state.chk_comp=False; st.session_state.chk_giros=False; st.session_state.chk_aisl=False
                        elif est_det=="NORMAL": st.session_state.chk_comp=True; st.session_state.chk_giros=True; st.session_state.chk_aisl=True
                        elif est_det=="GIROS": st.session_state.chk_comp=False; st.session_state.chk_giros=False; st.session_state.chk_aisl=True
                        elif est_det=="AISLADORES": st.session_state.chk_comp=False; st.session_state.chk_giros=True; st.session_state.chk_aisl=False

                    # UI PESTAÑAS
                    info = datos[it]; fr = info['fila_excel']; d = info['datos']
                    nm_p = safe_val(d, 6)
                    
                    t_res, t_cim, t_pos, t_men, t_ten = st.tabs(["📊 Resumen", "🧱 Cimentación", "🗼 Postes", "🔧 Ménsulas", "⚡ Tendidos"])
                    
                    # 1. RESUMEN
                    with t_res:
                        st.info(f"Resumen del Perfil **{it}** (Poste **{nm_p}**)")
                        c1, c2 = st.columns(2)
                        f_pos = safe_val(d, 8)
                        c1.metric("Cimentación", safe_val(d, 5) or "Pendiente")
                        c1.metric("Poste", f_pos if f_pos else "Pendiente")
                        c2.metric("Ménsula", safe_val(d, 38) or "Pendiente")
                        est_t = st.session_state.get("estado_tendido_actual", "NORMAL")
                        lbl_t = "Pendiente"
                        if est_t == "TENDIDO_AZUL": lbl_t = "🔵 TENDIDO"
                        elif est_t == "GRAPADO_VERDE": lbl_t = "✅ GRAPADO"
                        c2.metric("Cable LA-280", lbl_t)

                    # 2. CIMENTACION
                    with t_cim:
                        fc = safe_val(d, 5)
                        if fc: st.success(f"✅ Ejecutado: {fc}")
                        elif st.button("Grabar Cimentación", use_container_width=True):
                            guardar_prod_con_nota_compleja(nom, hj, fr, 5, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, bk)
                            st.rerun()

                    # 3. POSTES
                    with t_pos:
                        fp = safe_val(d, 8)
                        if st.session_state.chk_comp and fp: st.success(f"✅ Terminado: {fp}")
                        else:
                            cc1, cc2, cc3 = st.columns(3)
                            st.session_state.chk_giros = cc1.checkbox("Giros", value=st.session_state.chk_giros)
                            st.session_state.chk_aisl = cc2.checkbox("Aisladores", value=st.session_state.chk_aisl)
                            st.session_state.chk_comp = cc3.checkbox("Completo", value=st.session_state.chk_comp, on_change=on_completo_change)
                            if st.button("💾 Grabar Poste", use_container_width=True):
                                est = "NORMAL"
                                if not st.session_state.chk_giros: est = "GIROS"
                                if not st.session_state.chk_aisl: est = "AISLADORES"
                                guardar_prod_con_nota_compleja(nom, hj, fr, 8, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, bk, estilo_letra=est)
                                st.rerun()

                    # 4. MENSULAS
                    with t_men:
                        fm = safe_val(d, 38)
                        if fm: st.success(f"✅ Ejecutado: {fm}")
                        elif st.button("Grabar Ménsula", use_container_width=True):
                            guardar_prod_con_nota_compleja(nom, hj, fr, 38, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, bk)
                            st.rerun()

                    # 5. TENDIDOS
                    with t_ten:
                        st.subheader("⚡ Gestión de Tramos")
                        c1, c2 = st.columns(2)
                        p_ini = c1.selectbox("Desde:", list_perfiles, index=list_perfiles.index(it) if it in list_perfiles else 0)
                        p_fin = c2.selectbox("Hasta:", list_perfiles, index=list_perfiles.index(it) if it in list_perfiles else 0)
                        
                        b1, b2 = st.columns(2)
                        if b1.button("🔵 TENDIDO (Azul)", use_container_width=True):
                            procesar_tramo(nom, hj, bk, datos, list_perfiles, p_ini, p_fin, "TENDIDO_AZUL")
                        if b2.button("✅ GRAPADO (Verde)", use_container_width=True):
                            procesar_tramo(nom, hj, bk, datos, list_perfiles, p_ini, p_fin, "GRAPADO_VERDE")

def procesar_tramo(nom, hj, bk, datos, lista_perf, ini, fin, estilo):
    try:
        idx_a, idx_b = lista_perf.index(ini), lista_perf.index(fin)
        if idx_a > idx_b: idx_a, idx_b = idx_b, idx_a
        sublista = lista_perf[idx_a : idx_b + 1]
        
        bar = st.progress(0)
        hoy = datetime.now().strftime("%d/%m/%Y")
        
        for i, perf in enumerate(sublista):
            if perf in datos:
                fr = datos[perf]['fila_excel']
                val = hoy if (i==0 or i==len(sublista)-1) else ""
                guardar_prod_con_nota_compleja(nom, hj, fr, 39, val, st.session_state.veh_glob, bk, f"Tramo {ini}-{fin}", estilo)
                bar.progress((i+1)/len(sublista))
        st.success("Tramo registrado correctamenete.")
        time.sleep(1)
        st.rerun()
    except Exception as e: st.error(f"Error: {e}")

def mostrar_whatsapp():
    c_back, c_tit = st.columns([1, 4])
    with c_back:
        if st.button("⬅️ VOLVER AL MENÚ", key="back_wsp"): navigate_to("HOME")
    with c_tit:
        st.title("📲 Comunicaciones Seguras")
    
    st.info("Solo se permite enviar mensajes a la lista autorizada.")
    
    agenda_segura = {
        "Jefe de Obra": "972500000000",
        "Oficina Técnica": "972500000000",
        "Tablet Compañero": "972500000000"
    }
    
    dest = st.selectbox("Destinatario:", list(agenda_segura.keys()))
    num = agenda_segura[dest]
    
    txt = st.text_area("Mensaje:", height=150)
    
    if txt:
        msg_enc = urllib.parse.quote(txt)
        link = f"https://wa.me/{num}?text={msg_enc}"
        st.link_button(f"📨 ENVIAR A {dest}", link, type="primary", use_container_width=True)

# ==========================================
# 6. MOTOR PRINCIPAL DE LA APP
# ==========================================
if st.session_state.page == "HOME":
    mostrar_home()
elif st.session_state.page == "PARTES":
    mostrar_pantalla_partes()
elif st.session_state.page == "PRODUCCION":
    mostrar_pantalla_produccion()
elif st.session_state.page == "WHATSAPP":
    mostrar_whatsapp()

