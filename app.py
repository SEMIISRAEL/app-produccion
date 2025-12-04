import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time

# --- CONFIGURACIÓN ---
# EL NOMBRE EXACTO DE TU ARCHIVO EN DRIVE:
NOMBRE_ARCHIVO_NUBE = 'Roster 2025 12 (empty)' 

# Función de Conexión
def conectar():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(NOMBRE_ARCHIVO_NUBE).sheet1

# --- FUNCIÓN: LEER NOMBRES AUTOMÁTICAMENTE ---
def obtener_lista_nombres():
    try:
        sh = conectar()
        # Leemos toda la Columna A (la 1)
        columna_a = sh.col_values(1)
        
        # Como tu cabecera está en la fila 4, los nombres empiezan en la fila 5.
        # En Python: índice 0=Fila1, 3=Fila4... empezamos desde el índice 4 (Fila 5)
        nombres_sucios = columna_a[4:] 
        
        # Filtramos para quitar huecos vacíos
        nombres_limpios = [n for n in nombres_sucios if n != "" and n != None]
        
        # Ordenamos alfabéticamente para que sea más fácil buscar
        return sorted(nombres_limpios)
    except Exception as e:
        return []

# Función de Escritura (Busca días en Fila 4)
def escribir_horas(nombre, fecha, horas):
    try:
        sh = conectar()
        
        # 1. Buscar Fila del Operario
        cell_nombre = sh.find(nombre)
        if not cell_nombre:
            return f"❌ No encuentro a '{nombre}' en la Columna A"
        fila = cell_nombre.row
        
        # 2. Buscar Columna del Día (En la Fila 4)
        dia = str(fecha.day)
        # in_row=4 obliga a buscar SOLO en la fila 4 (tu cabecera de días)
        cell_dia = sh.find(dia, in_row=4) 
        if not cell_dia:
            return f"❌ No encuentro el día '{dia}' en la Fila 4 (Cabecera)"
        col = cell_dia.col
        
        # 3. Escribir
        sh.update_cell(fila, col, horas)
        return True
    except Exception as e:
        return f"Error técnico: {str(e)}"

# --- PANTALLA DE LA TABLET ---
st.set_page_config(page_title="Roster 2025", page_icon="📝")
st.title("📝 Fichar en Roster 2025")

col1, col2 = st.columns(2)

# Cargamos los nombres del Excel automáticamente
with st.spinner("Cargando personal..."):
    lista_dinamica = obtener_lista_nombres()

with col1:
    if not lista_dinamica:
        st.error("⚠️ No pude leer nombres. Revisa que el archivo en Drive sea una 'Hoja de Cálculo de Google' y no un Excel (.xlsx) subido tal cual.")
        nombre = st.selectbox("Operario", ["Error de lectura"])
    else:
        nombre = st.selectbox("Selecciona Operario", lista_dinamica)

with col2:
    fecha = st.date_input("Fecha", datetime.now())
    horas = st.number_input("Horas", min_value=0.0, max_value=24.0, step=0.5)

st.divider()

if st.button("💾 GUARDAR HORAS", type="primary", use_container_width=True):
    if nombre == "Error de lectura":
        st.error("No se puede guardar sin operario.")
    else:
        with st.spinner(f"Guardando en {NOMBRE_ARCHIVO_NUBE}..."):
            res = escribir_horas(nombre, fecha, horas)
            if res == True:
                st.success(f"✅ ¡Guardado! {horas}h para {nombre} el día {fecha.day}")
                time.sleep(2) # Pausa para que se vea el mensaje
                st.rerun() # Recarga para limpiar
            else:
                st.error(res)
