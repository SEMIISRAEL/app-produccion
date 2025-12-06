import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from google.oauth2 import service_account
from datetime import datetime
import time
import urllib.parse
from gspread.utils import rowcol_to_a1

# ==========================================
# 1. CONFIGURACIÓN E INYECCIÓN DE ESTILO
# ==========================================
st.set_page_config(page_title="SEMI Tablet", layout="wide", page_icon="🏗️")

st.markdown("""
<style>
    .big-button { width: 100%; height: 120px; border-radius: 15px; font-size: 20px; font-weight: bold; margin-bottom: 10px; }
    .stButton>button { width: 100%; border-radius: 10px; height: 3em; font-weight: bold; }
    .main-title { text-align: center; font-size: 2.5rem; color: #333; margin-bottom: 20px; }
    .status-box { padding: 15px; border-radius: 10px; border: 1px solid #ddd; margin-bottom: 10px; }
</style>
""", unsafe_allow_html=True)

# --- IDs FIJOS (REVISA QUE SEAN CORRECTOS) ---
ID_VEHICULOS = "19PWpeCz8pl5NEDpK-omX5AdrLuJgOPrn6uSjtUGomY8"
ID_CONFIG_PROD = "1uCu5pq6l1CjqXKPEkGkN-G5Z5K00qiV9kR_bGOii6FU"

# ==========================================
# 2. GESTIÓN DE ESTADO
# ==========================================
if 'page' not in st.session_state: st.session_state.page = "HOME"
if 'user_name' not in st.session_state: st.session_state.user_name = "Encargado Tablet"
if 'ID_ROSTER_ACTIVO' not in st.session_state: st.session_state.ID_ROSTER_ACTIVO = None
if 'TRAMO_ACTIVO' not in st.session_state: st.session_state.TRAMO_ACTIVO = None
if 'ARCH_PROD' not in st.session_state: st.session_state.ARCH_PROD = None
if 'ARCH_BACKUP' not in st.session_state: st.session_state.ARCH_BACKUP = None
if 'veh_glob' not in st.session_state: st.session_state.veh_glob = None
if 'lista_sel' not in st.session_state: st.session_state.lista_sel = []
if 'prod_dia' not in st.session_state: st.session_state.prod_dia = {}

# Variables Checkbox
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
# 3. CONEXIÓN Y ROBOT (FORMATOS)
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
        else: cambiar_formato_google(ws, fila, col, "NORMAL")
        return True
    except Exception as e:
        st.error(f"Error: {e}")
        return False

# ==========================================
# 4. CARGA DE DATOS
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
            item_id = str(fila[0]).strip() # Columna A: Perfil
            # Evitamos filas vacías o cabeceras
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

@st.cache_data(ttl=600)
def obtener_hojas_track_cached(nombre_archivo):
    sh = conectar_flexible(nombre_archivo)
    if not sh: return None
    try: return [ws.title for ws in sh.worksheets() if "HR TRACK" in ws.title.upper()]
    except: return []

# ==========================================
# 5. BARRA LATERAL GLOBAL (IMPORTANTE: Mover aquí)
# ==========================================
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2942/2942813.png", width=100)
    st.markdown("### ⚙️ Configuración")
    
    # Carga de Roster
    archivos_roster = buscar_archivos_roster()
    if archivos_roster:
        nombre_roster_sel = list(archivos_roster.keys())[0]
        st.session_state.ID_ROSTER_ACTIVO = archivos_roster[nombre_roster_sel]
    
    # Carga de Tramos
    conf_prod = cargar_config_prod()
    if conf_prod:
        st.write("Selecciona tu Tramo de trabajo:")
        # Intentamos mantener la selección si ya existe
        idx_tramo = list(conf_prod.keys()).index(st.session_state.TRAMO_ACTIVO) if st.session_state.TRAMO_ACTIVO in conf_prod else 0
        tramo_sel = st.selectbox("Tramo Activo:", list(conf_prod.keys()), index=idx_tramo, key="sidebar_tramo")
        
        if tramo_sel:
            st.session_state.TRAMO_ACTIVO = tramo_sel
            st.session_state.ARCH_PROD, st.session_state.ARCH_BACKUP = conf_prod.get(tramo_sel)
            st.success(f"✅ Conectado a: {tramo_sel}")
    else:
        st.error("No se encontró configuración de tramos.")

    st.markdown("---")
    if st.button("🚪 Cerrar Sesión"):
        st.session_state.logged_in = False
        st.rerun()

# ==========================================
# 6. PANTALLAS
# ==========================================

def mostrar_home():
    st.markdown("<h1 class='main-title'>PANEL DE CONTROL DE OBRA</h1>", unsafe_allow_html=True)
    
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### 📝 Personal")
        if st.button("PARTES DIARIOS", type="primary", use_container_width=True): navigate_to("PARTES")
    with c2:
        st.markdown("### 🏗️ Producción")
        if st.button("REGISTRO DE OBRA", type="primary", use_container_width=True): navigate_to("PRODUCCION")
    
    st.markdown("---")
    c3, c4 = st.columns(2)
    with c3:
        if st.button("📲 WHATSAPP INTERNO", use_container_width=True): navigate_to("WHATSAPP")
    with c4:
        if st.button("🔄 RECARGAR APP", use_container_width=True): st.rerun()

def mostrar_pantalla_partes():
    c_back, c_tit = st.columns([1, 4])
    with c_back:
        if st.button("⬅️ VOLVER"): navigate_to("HOME")
    with c_tit:
        st.title("📝 Partes de Trabajo")
    
    # Selector de Vehículo (Fundamental)
    dv = cargar_vehiculos_dict()
    nv = [""] + list(dv.keys()) if dv else ["Error"]
    ve = st.selectbox("🚛 Selecciona Vehículo / Lugar:", nv, index=nv.index(st.session_state.veh_glob) if st.session_state.veh_glob in nv else 0)
    st.session_state.veh_glob = ve
    
    if st.session_state.ID_ROSTER_ACTIVO and ve:
        st.success(f"Trabajando en: {ve}")
        # Aquí iría la lógica de añadir trabajadores (simplificada para no alargar)
        st.info("Sistema de partes listo. (Código simplificado para esta vista)")

def mostrar_pantalla_produccion():
    c_back, c_tit = st.columns([1, 4])
    with c_back:
        if st.button("⬅️ VOLVER"): navigate_to("HOME")
    with c_tit:
        st.title("🏗️ Registro de Producción")

    # --- SALVAVIDAS: Selector de Vehículo si no está seleccionado ---
    if not st.session_state.veh_glob:
        st.warning("⚠️ No has seleccionado vehículo.")
        dv = cargar_vehiculos_dict()
        nv = [""] + list(dv.keys()) if dv else ["Error"]
        ve = st.selectbox("Selecciona Vehículo ahora:", nv)
        if ve:
            st.session_state.veh_glob = ve
            st.rerun()
        return # Esperamos a que seleccione

    if not st.session_state.TRAMO_ACTIVO:
        st.error("⚠️ Selecciona un Tramo en la barra lateral izquierda.")
        return

    nom = st.session_state.ARCH_PROD
    bk = st.session_state.ARCH_BACKUP
    hjs = obtener_hojas_track_cached(nom)
    
    if hjs:
        hj = st.selectbox("Seleccionar Hoja de Control:", hjs)
        if hj:
            with st.spinner("Cargando datos..."):
                datos = cargar_datos_completos_hoja(nom, hj)
            
            if datos:
                list_perfiles_ordenada = list(datos.keys())
                todos_v = datos.values()
                
                # Listas para filtros
                list_cim = sorted(list(set(d['datos'][2] for d in todos_v if len(d['datos'])>2 and d['datos'][2])))
                list_post = sorted(list(set(d['datos'][5] for d in todos_v if len(d['datos'])>5 and d['datos'][5])))
                
                with st.expander("🔍 Filtros de Búsqueda", expanded=True):
                    cf1, cf2, cf3 = st.columns(3)
                    fil_cim = cf1.selectbox("Cimentación", ["Todos"] + list_cim)
                    fil_post = cf2.selectbox("Tipo Poste", ["Todos"] + list_post)
                    fil_km = cf3.text_input("Buscar Km:")
                
                keys_ok = []
                for k, inf in datos.items():
                    r = inf['datos']
                    if fil_km and fil_km not in str(k): continue
                    if fil_cim != "Todos" and (len(r)<=2 or r[2]!=fil_cim): continue
                    if fil_post != "Todos" and (len(r)<=5 or r[5]!=fil_post): continue
                    keys_ok.append(k)
                
                it = st.selectbox("📍 SELECCIONAR PERFIL / POSTE:", keys_ok)
                
                if it:
                    # LOGICA ROBOT
                    if st.session_state.last_item_loaded != it:
                        st.session_state.last_item_loaded = it
                        info = datos[it]; fr, d = info['fila_excel'], info['datos']
                        fp = safe_val(d, 8)
                        
                        est_det = "NORMAL"; est_ten = "NORMAL"
                        if fp or safe_val(d, 39):
                            try:
                                sh_t = conectar_flexible(nom); ws_t = sh_t.worksheet(hj)
                                if fp: est_det = detectar_estilo_celda(ws_t, fr, 8)
                                est_ten = detectar_estilo_celda(ws_t, fr, 39)
                            except: pass
                        
                        st.session_state.estado_tendido_actual = est_ten
                        if not fp: st.session_state.chk_comp=False; st.session_state.chk_giros=False; st.session_state.chk_aisl=False
                        elif est_det=="NORMAL": st.session_state.chk_comp=True; st.session_state.chk_giros=True; st.session_state.chk_aisl=True
                        elif est_det=="GIROS": st.session_state.chk_comp=False; st.session_state.chk_giros=False; st.session_state.chk_aisl=True
                        elif est_det=="AISLADORES": st.session_state.chk_comp=False; st.session_state.chk_giros=True; st.session_state.chk_aisl=False

                    # UI
                    info = datos[it]; fr = info['fila_excel']; d = info['datos']; nm_p = safe_val(d, 6)
                    
                    t_res, t_cim, t_pos, t_men, t_ten = st.tabs(["📊 Resumen", "🧱 Cimentación", "🗼 Postes", "🔧 Ménsulas", "⚡ Tendidos"])
                    
                    with t_res:
                        st.markdown(f"### Perfil {it} (Poste {nm_p})")
                        c1, c2 = st.columns(2)
                        f_pos = safe_val(d, 8)
                        c1.info(f"Cimentación: {safe_val(d, 5) or 'Pendiente'}")
                        if f_pos: 
                            if st.session_state.chk_comp: c1.success(f"Poste: {f_pos}")
                            else: c1.warning(f"Poste: {f_pos} (Incompleto)")
                        else: c1.error("Poste: Pendiente")
                        
                        c2.info(f"Ménsula: {safe_val(d, 38) or 'Pendiente'}")
                        est_t = st.session_state.get("estado_tendido_actual", "NORMAL")
                        if est_t == "TENDIDO_AZUL": c2.info("Cable: 🔵 TENDIDO")
                        elif est_t == "GRAPADO_VERDE": c2.success("Cable: ✅ GRAPADO")
                        else: c2.error("Cable: Pendiente")

                    with t_cim:
                        fc = safe_val(d, 5)
                        if fc: st.success(f"✅ Ejecutado: {fc}")
                        elif st.button("Grabar Cimentación", use_container_width=True):
                            guardar_prod_con_nota_compleja(nom, hj, fr, 5, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, bk)
                            st.session_state.prod_dia.setdefault(it, []).append("CIM"); st.rerun()

                    with t_pos:
                        fp = safe_val(d, 8)
                        if st.session_state.chk_comp and fp: st.success(f"✅ Terminado: {fp}")
                        else:
                            c_a, c_b, c_c = st.columns(3)
                            st.session_state.chk_giros = c_a.checkbox("Giros", value=st.session_state.chk_giros)
                            st.session_state.chk_aisl = c_b.checkbox("Aisladores", value=st.session_state.chk_aisl)
                            st.session_state.chk_comp = c_c.checkbox("Completo", value=st.session_state.chk_comp, on_change=on_completo_change)
                            if st.button("💾 Grabar Poste", use_container_width=True):
                                est = "NORMAL"; txt = ""
                                if not st.session_state.chk_giros: est = "GIROS"; txt += "Faltan Giros. "
                                if not st.session_state.chk_aisl: est = "AISLADORES"; txt += "Faltan Aisladores. "
                                if st.session_state.chk_comp: est = "NORMAL"
                                guardar_prod_con_nota_compleja(nom, hj, fr, 8, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, bk, txt, estilo_letra=est)
                                st.session_state.prod_dia.setdefault(it, []).append("POSTE"); st.rerun()

                    with t_men:
                        fm = safe_val(d, 38)
                        if fm: st.success(f"✅ Ejecutado: {fm}")
                        elif st.button("Grabar Ménsula", use_container_width=True):
                            guardar_prod_con_nota_compleja(nom, hj, fr, 38, datetime.now().strftime("%d/%m/%Y"), st.session_state.veh_glob, bk)
                            st.session_state.prod_dia.setdefault(it, []).append("MEN"); st.rerun()

                    with t_ten:
                        st.subheader("⚡ Tramos de Cable")
                        c1, c2 = st.columns(2)
                        p_ini = c1.selectbox("Desde:", list_perfiles_ordenada, index=list_perfiles_ordenada.index(it) if it in list_perfiles_ordenada else 0)
                        p_fin = c2.selectbox("Hasta:", list_perfiles_ordenada, index=list_perfiles_ordenada.index(it) if it in list_perfiles_ordenada else 0)
                        
                        b1, b2 = st.columns(2)
                        if b1.button("🔵 TENDIDO (Azul)", use_container_width=True):
                            procesar_tramo(nom, hj, bk, datos, list_perfiles_ordenada, p_ini, p_fin, "TENDIDO_AZUL", it)
                        if b2.button("✅ GRAPADO (Verde)", use_container_width=True):
                            procesar_tramo(nom, hj, bk, datos, list_perfiles_ordenada, p_ini, p_fin, "GRAPADO_VERDE", it)

def procesar_tramo(nom, hj, bk, datos, lista_perf, ini, fin, estilo, it_act):
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
        
        st.success("Tramo registrado.")
        time.sleep(1)
        st.session_state.prod_dia.setdefault(it_act, []).append(f"TRAMO {estilo} ({ini}-{fin})")
        st.rerun()
    except Exception as e: st.error(f"Error: {e}")

def mostrar_whatsapp():
    c_back, c_tit = st.columns([1, 4])
    with c_back:
        if st.button("⬅️ VOLVER"): navigate_to("HOME")
    with c_tit:
        st.title("🔒 WhatsApp Seguro")
    
    agenda_segura = {
        "Tablet 01": "972000000001",
        "Tablet 02": "972000000002",
        "Oficina": "972000000000"
    }
    
    c1, c2 = st.columns([2, 1])
    dest = c1.selectbox("Destinatario:", list(agenda_segura.keys()))
    
    if 'mensaje_base' not in st.session_state:
        res = "\n".join([f"{k}: {v}" for k,v in st.session_state.prod_dia.items()]) if st.session_state.prod_dia else "Sin datos."
        st.session_state.mensaje_base = f"*REPORTE {datetime.now().strftime('%d/%m')}*\n----------------\n{res}"
    
    txt = st.text_area("Mensaje:", value=st.session_state.mensaje_base, height=200)
    
    if txt:
        msg_enc = urllib.parse.quote(txt)
        num = agenda_segura[dest]
        st.link_button(f"📨 ENVIAR A {dest}", f"https://wa.me/{num}?text={msg_enc}", type="primary", use_container_width=True)

# ==========================================
# 7. MOTOR PRINCIPAL
# ==========================================
if st.session_state.page == "HOME": mostrar_home()
elif st.session_state.page == "PARTES": mostrar_pantalla_partes()
elif st.session_state.page == "PRODUCCION": mostrar_pantalla_produccion()
elif st.session_state.page == "WHATSAPP": mostrar_whatsapp()
