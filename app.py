import io
import zipfile
import concurrent.futures as cf
 
import requests
import streamlit as st
 
# =====================================================================
#  Descargador de fotos de producto (Distrinando)
#
#  Dos fuentes:
#   1) AZURE   -> blob del catalogo interno
#                 https://distriecomm.blob.core.windows.net/catalogo/<Marca>/<sku>-<n>.jpg
#   2) SHOPIFY -> CDN de las tiendas online
#                 https://<dominio>/cdn/shop/files/<sku>-<n>.jpg
#
#  En ambos casos: pegas un listado de SKU (uno por linea), detecta la
#  marca por el prefijo y baja TODAS las fotos (-1, -2, -3...), sean
#  correlativas o con saltos.
# =====================================================================
 
st.set_page_config(page_title="Descargar fotos", page_icon="📷", layout="wide")
 
# ---------------------------------------------------------------------
#  Marcas: prefijo -> nombre. El ORDEN importa (primero los de 3
#  letras: RBK, COL; despues los de 1: C, K, P), igual que en la macro.
# ---------------------------------------------------------------------
MARCAS = [
    ("RBK", "Reebok"),
    ("COL", "Columbia"),
    ("C",   "Crocs"),
    ("K",   "Kappa"),
    ("P",   "Piccadilly"),
]
 
# Carpeta en el blob de Azure por marca
AZURE_BASE = "https://distriecomm.blob.core.windows.net/catalogo/"
AZURE_CARPETA = {
    "Reebok": "Reebok",
    "Columbia": "Columbia",
    "Crocs": "Crocs",
    "Kappa": "Kappa",
    "Piccadilly": "Piccadilly",
}
 
# Dominios de las tiendas Shopify por marca (editables en la UI).
# Solo Crocs esta confirmado; completa los demas una vez.
SHOPIFY_DOMINIOS_DEFAULT = {
    "Reebok": "reebok.com.ar",
    "Columbia": "columbiasportswear.com.ar",
    "Crocs": "www.crocs.com.ar",
    "Kappa": "www.kappastore.com.ar",
    "Piccadilly": "www.piccadilly.com.ar",
}
 
# Rutas que usa Shopify para servir imagenes (se prueban en orden)
SHOPIFY_PATHS = ["/cdn/shop/files/", "/cdn/shop/products/"]
 
 
def detectar_marca(codigo: str):
    up = codigo.strip().upper()
    for prefijo, marca in MARCAS:
        if up.startswith(prefijo):
            return marca
    return None
 
 
def urls_candidatas(codigo, marca, n, ext, fuente, dominios):
    """Lista de URLs posibles para la foto <codigo>-<n><ext>."""
    if fuente == "Azure":
        carpeta = AZURE_CARPETA.get(marca)
        if not carpeta:
            return []
        return [f"{AZURE_BASE}{carpeta}/{codigo}-{n}{ext}"]
    else:  # Shopify
        dom = (dominios.get(marca) or "").strip().rstrip("/")
        dom = dom.replace("https://", "").replace("http://", "")
        if not dom:
            return []
        return [f"https://{dom}{path}{codigo}-{n}{ext}" for path in SHOPIFY_PATHS]
 
 
# ---------------------------------------------------------------------
#  Descarga de una sola foto (para correr en paralelo)
# ---------------------------------------------------------------------
def bajar_una(session, codigo, marca, n, exts, fuente, dominios):
    for ext in exts:
        for url in urls_candidatas(codigo, marca, n, ext, fuente, dominios):
            try:
                r = session.get(url, timeout=20)
            except requests.RequestException:
                continue
            if r.status_code == 200 and r.content and len(r.content) > 100:
                ct = r.headers.get("Content-Type", "")
                if ct.startswith("image") or not ct.startswith(("text", "application/xml", "application/json")):
                    return {
                        "codigo": codigo,
                        "n": n,
                        "nombre": f"{codigo}-{n}{ext}",
                        "contenido": r.content,
                        "url": url,
                    }
    return None
 
 
def procesar(articulos, max_fotos, exts, workers, fuente, dominios):
    encontrados, sin_marca, sin_fotos = [], [], []
 
    tareas = []
    for codigo in articulos:
        marca = detectar_marca(codigo)
        if marca is None:
            sin_marca.append(codigo)
            continue
        # Si es Shopify y no hay dominio cargado para esa marca -> sin_marca
        if fuente == "Shopify" and not (dominios.get(marca) or "").strip():
            sin_marca.append(codigo)
            continue
        for n in range(1, max_fotos + 1):
            tareas.append((codigo, marca, n))
 
    if not tareas:
        return encontrados, sin_marca, sin_fotos
 
    session = requests.Session()
    session.headers.update({"User-Agent": "Distrinando-FotoDownloader/1.0"})
 
    progreso = st.progress(0.0, text="Buscando fotos...")
    total = len(tareas)
    hechas = 0
 
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futuros = [
            ex.submit(bajar_una, session, c, m, n, exts, fuente, dominios)
            for (c, m, n) in tareas
        ]
        for fut in cf.as_completed(futuros):
            res = fut.result()
            if res:
                encontrados.append(res)
            hechas += 1
            progreso.progress(hechas / total, text=f"Buscando fotos... {hechas}/{total}")
 
    progreso.empty()
 
    con_foto = {e["codigo"] for e in encontrados}
    for codigo in articulos:
        marca = detectar_marca(codigo)
        if marca is None:
            continue
        if fuente == "Shopify" and not (dominios.get(marca) or "").strip():
            continue
        if codigo not in con_foto:
            sin_fotos.append(codigo)
 
    encontrados.sort(key=lambda e: (e["codigo"], e["n"]))
    return encontrados, sin_marca, sin_fotos
 
 
def armar_zip(encontrados, por_carpeta):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for e in encontrados:
            ruta = f"{e['codigo']}/{e['nombre']}" if por_carpeta else e["nombre"]
            z.writestr(ruta, e["contenido"])
    return buf.getvalue()
 
 
# =====================================================================
#  INTERFAZ
# =====================================================================
st.title("📷 Descargar fotos de producto")
 
fuente = st.radio(
    "Fuente de las fotos",
    ["Azure", "Shopify"],
    horizontal=True,
    help="Azure = blob del catalogo interno. Shopify = CDN de las tiendas online.",
)
 
st.caption(
    "Pega los SKU (uno por linea). Detecta la marca por el prefijo y baja "
    "TODAS las fotos (-1, -2, -3...), aunque haya saltos."
)
 
col_izq, col_der = st.columns([2, 1])
 
with col_izq:
    texto = st.text_area(
        "SKU (uno por linea)",
        height=260,
        placeholder=(
            "C10001-C5CI\nRBK100033738\nCOL1234ABC\nK123456\nP987654"
            if fuente == "Shopify"
            else "RBK100033738\nCOL1234ABC\nC205089\nK123456\nP987654"
        ),
    )
 
with col_der:
    st.markdown("**Opciones**")
    max_fotos = st.slider("Maximo de fotos por SKU", 1, 30, 12)
    exts_sel = st.multiselect(
        "Extensiones a probar",
        [".jpg", ".jpeg", ".png", ".webp"],
        default=[".jpg"],
    )
    workers = st.slider("Descargas en paralelo", 4, 40, 16)
 
# Dominios de tiendas (solo para Shopify)
dominios = dict(SHOPIFY_DOMINIOS_DEFAULT)
if fuente == "Shopify":
    with st.expander("🌐 Dominios de las tiendas Shopify (completar una vez)", expanded=True):
        st.caption("Solo el dominio, sin https:// (ej: www.crocs.com.ar).")
        d1, d2, d3 = st.columns(3)
        d4, d5, _ = st.columns(3)
        dominios["Crocs"] = d1.text_input("Crocs (C)", value=SHOPIFY_DOMINIOS_DEFAULT["Crocs"])
        dominios["Reebok"] = d2.text_input("Reebok (RBK)", value=SHOPIFY_DOMINIOS_DEFAULT["Reebok"], placeholder="www.reebok.com.ar")
        dominios["Columbia"] = d3.text_input("Columbia (COL)", value=SHOPIFY_DOMINIOS_DEFAULT["Columbia"], placeholder="www.columbia.com.ar")
        dominios["Kappa"] = d4.text_input("Kappa (K)", value=SHOPIFY_DOMINIOS_DEFAULT["Kappa"], placeholder="www.kappa.com.ar")
        dominios["Piccadilly"] = d5.text_input("Piccadilly (P)", value=SHOPIFY_DOMINIOS_DEFAULT["Piccadilly"], placeholder="www.piccadilly.com.ar")
 
agrupacion = st.radio(
    "¿Cómo querés el ZIP?",
    ["Una carpeta por artículo", "Todas las fotos juntas (sin carpetas)"],
    horizontal=True,
)
por_carpeta = agrupacion.startswith("Una carpeta")
 
buscar = st.button("Buscar fotos", type="primary", use_container_width=True)
 
# ---------------------------------------------------------------------
#  Al buscar: procesar y guardar en session_state (el resultado y el
#  boton de descarga sobreviven al recargar cuando se aprieta Descargar).
# ---------------------------------------------------------------------
if buscar:
    articulos = [l.strip() for l in texto.splitlines() if l.strip()]
    vistos = set()
    articulos = [a for a in articulos if not (a in vistos or vistos.add(a))]
 
    if not articulos:
        st.warning("Pega al menos un SKU.")
        st.session_state.pop("resultado", None)
    elif not exts_sel:
        st.warning("Elegi al menos una extension.")
        st.session_state.pop("resultado", None)
    else:
        encontrados, sin_marca, sin_fotos = procesar(
            articulos, max_fotos, exts_sel, workers, fuente, dominios
        )
        st.session_state["resultado"] = {
            "articulos": articulos,
            "encontrados": encontrados,
            "sin_marca": sin_marca,
            "sin_fotos": sin_fotos,
            "fuente": fuente,
        }
 
# ---------------------------------------------------------------------
#  Mostrar el ultimo resultado guardado
# ---------------------------------------------------------------------
res = st.session_state.get("resultado")
if res:
    articulos = res["articulos"]
    encontrados = res["encontrados"]
    sin_marca = res["sin_marca"]
    sin_fotos = res["sin_fotos"]
 
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("SKU", len(articulos))
    c2.metric("Fotos encontradas", len(encontrados))
    c3.metric("Sin fotos", len(sin_fotos))
    c4.metric("Sin dominio / marca", len(sin_marca))
 
    if encontrados:
        zip_bytes = armar_zip(encontrados, por_carpeta)
        st.success(f"✅ {len(encontrados)} fotos listas para descargar.")
        st.download_button(
            "⬇️ Descargar todas las fotos (.zip)",
            data=zip_bytes,
            file_name="fotos.zip",
            mime="application/zip",
            type="primary",
            use_container_width=True,
        )
 
        resumen = {}
        for e in encontrados:
            resumen.setdefault(e["codigo"], []).append(e["n"])
        with st.expander("Detalle por SKU", expanded=True):
            for codigo, nums in resumen.items():
                st.write(f"**{codigo}** — {len(nums)} fotos: {sorted(nums)}")
 
        st.subheader("Vista previa")
        cols = st.columns(6)
        for i, e in enumerate(encontrados):
            with cols[i % 6]:
                st.image(e["contenido"], caption=e["nombre"], use_container_width=True)
 
    if sin_fotos:
        st.warning("Sin ninguna foto encontrada: " + ", ".join(sin_fotos))
    if sin_marca:
        st.error(
            "Sin dominio cargado o marca no detectada (revisar prefijo/dominio): "
            + ", ".join(sin_marca)
        )
 

