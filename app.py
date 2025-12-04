import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time

# --- CONFIGURACIÓN ---
# Aquí le decimos que busque en los secretos de la nube (luego te enseño a ponerlo)
# OJO: Nombre exacto de la hoja en Drive:
NOMBRE_ARCHIVO_NUBE = 'Roster 2025 12 (empty)' 

# Función para conectar (con sistema de secretos de Streamlit)
def conectar():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    # Esta línea mágica lee el JSON desde la caja fuerte de Streamlit (no del archivo local)
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client.open(NOMBRE_ARCHIVO_NUBE).sheet1

def escribir_horas(nombre, fecha, horas):
    try:
        sh = conectar()
        
        # 1. Buscar la Fila (Nombre)
        cell_nombre = sh.find(nombre)
        if not cell_nombre:
            return f"❌ No encuentro a {nombre}"
        fila = cell_nombre.row
        
        # 2. Buscar la Columna (Día)
        dia = str(fecha.day)
        # Busca en la fila 1 (cabeceras). Si tus días están en la fila 2, cambia a in_row=2
        cell_dia = sh.find(dia, in_row=1) 
        if not cell_dia:
            return f"❌ No encuentro el día {dia} en la cabecera"
        col = cell_dia.col
        
        # 3. Escribir
        sh.update_cell(fila, col, horas)
        return True
    except Exception as e:
        return f"Error técnico: {str(e)}"

# --- PANTALLA DE LA TABLET ---
st.set_page_config(page_title="Roster Directo", page_icon="✍️")
st.title("✍️ Fichar en Roster")

# Formulario
col1, col2 = st.columns(2)
# LISTA DE NOMBRES: Puedes ponerlos a mano aquí o leerlos de una hoja aparte
lista_nombres = ["GEORGI  IVANOV", "Ana García", "Pedro López", "Carlos Ruiz", "Operario 5"] 
nombre = col1.selectbox("Operario", lista_nombres)
fecha = col2.date_input("Fecha", datetime.now())
horas = st.number_input("Horas", min_value=0.0, max_value=24.0, step=0.5)

if st.button("GUARDAR", type="primary", use_container_width=True):
    with st.spinner("Guardando..."):
        res = escribir_horas(nombre, fecha, horas)
        if res == True:
            st.success(f"✅ Guardado: {horas}h para {nombre} el día {fecha.day}")
        else:

            st.error(res)

