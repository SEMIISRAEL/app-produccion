import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time

# --- CONFIGURACIÓN ---
NOMBRE_ARCHIVO_NUBE = 'Roster 2025 12 (empty)' 

# Función de Conexión
def conectar():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(NOMBRE_ARCHIVO_NUBE).sheet1

# --- FUNCIÓN CORREGIDA: LEER COLUMNA B ---
def obtener_lista_nombres():
    try:
        sh = conectar()
        # ¡CORRECCIÓN!: Leemos la Columna B (índice 2), no la A
        columna_b = sh.col_values(2)
        
        # Tu cabecera está en la fila 4, así que los datos empiezan en la 5.
        # Python cuenta desde 0, así que saltamos los primeros 4 elementos.
        nombres_sucios = columna_b[4:] 
        
        # Filtramos huecos vacíos
        nombres_limpios = [n for n in nombres_sucios if n != "" and n != None]
        
        return sorted(nombres_limpios)
    except Exception as e:
        return []

# Función de Escritura
def escribir_horas(nombre, fecha, horas):
    try:
        sh = conectar()
        
        # 1. Buscar al Operario (ahora lo buscará correctamente por nombre)
        cell_nombre = sh.find(nombre)
        if not cell_nombre:
            return f"❌ No encuentro a '{nombre}' en el archivo."
        fila = cell_nombre.row
        
        # 2. Buscar Columna del Día (En la Fila 4)
        dia = str(fecha.day)
        # Busca el día solo en la fila 4
        cell_dia = sh.find(dia, in_row=4) 
        if not cell_dia:
            return f"❌ No encuentro el día '{dia}' en la Fila 4."
        col = cell_dia.col
        
        # 3. Escribir
        sh.update_cell(fila, col, horas)
        return True
    except Exception as e:
        return f"Error técnico: {str(e)}"

# --- PANTALLA ---
st.set_page_config(page_title="Roster 2025", page_icon="📝")
st.title("📝 Fichar en Roster 2025")

col1, col2 = st.columns(2)

with st.spinner("Cargando nombres de la Columna B..."):
    lista_dinamica = obtener_lista_nombres()

with col1:
    if not lista_dinamica:
        st.error("⚠️ No encontré nombres. Revisa que el archivo en Drive sea 'Hoja de Google'.")
        nombre = st.selectbox("Operario", ["Error lectura"])
    else:
        nombre = st.selectbox("Selecciona Operario", lista_dinamica)

with col2:
    fecha = st.date_input("Fecha", datetime.now())
    horas = st.number_input("Horas", min_value=0.0, max_value=24.0, step=0.5)

st.divider()

if st.button("💾 GUARDAR HORAS", type="primary", use_container_width=True):
    if nombre == "Error lectura":
        st.error("No se puede guardar.")
    else:
        with st.spinner(f"Escribiendo en la fila de {nombre}..."):
            res = escribir_horas(nombre, fecha, horas)
            if res == True:
                st.success(f"✅ Guardado: {horas}h para {nombre} (Día {fecha.day})")
                time.sleep(2)
                st.rerun()
            else:
                st.error(res)
