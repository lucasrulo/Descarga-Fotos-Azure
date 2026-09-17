import io
import zipfile
import concurrent.futures as cf

import requests
import streamlit as st

# =====================================================================
#  Descargador de fotos desde Azure Blob (Distrinando)
#  Replica la logica de la macro InsertarFotosDesdeAzure, pero baja
#  TODAS las fotos de cada articulo (-1, -2, -3, -4...), sean
#  correlativas o tengan saltos.
# =====================================================================

st.set_page_config(page_title="Descargar fotos Azure", page_icon="📷", layout="wide")

# ---------------------------------------------------------------------
#  Configuracion de marcas: prefijo -> carpeta en el blob
#  El ORDEN importa: primero los prefijos de 3 letras (RBK, COL),
#  despues los de 1 letra (C, K, P), igual que en la macro.
# ---------------------------------------------------------------------
BASE_URL = "https://distriecomm.blob.core.windows.net/catalogo/"

MARCAS = [
    ("RBK", "Reebok"),
    ("COL", "Columbia"),
    ("C",   "Crocs"),
    ("K",   "Kappa"),
    ("P",   "Piccadilly"),
]


def detectar_carpeta(codigo: str):
    """Devuelve la carpeta (marca) segun el prefijo del codigo, o None."""
    up = codigo.strip().upper()
    for prefijo, carpeta in MARCAS:
        if up.startswith(prefijo):
            return carpeta
    return None


def construir_url(codigo: str, carpeta: str, n: int, ext: str) -> str:
    return f"{BASE_URL}{carpeta}/{codigo}-{n}{ext}"


# ---------------------------------------------------------------------
#  Descarga de una sola foto (para correr en paralelo)
# ---------------------------------------------------------------------
def bajar_una(session: requests.Session, codigo: str, carpeta: str, n: int, exts):
    for ext in exts:
        url = construir_url(codigo, carpeta, n, ext)
        try:
            r = session.get(url, timeout=15)
        except requests.RequestException:
            continue
        if r.status_code == 200 and r.content and len(r.content) > 100:
            ct = r.headers.get("Content-Type", "")
            # Descartar respuestas XML de error de Azure aunque den 200
            if ct.startswith("image") or not ct.startswith(("text", "application/xml")):
                return {
                    "codigo": codigo,
                    "n": n,
                    "nombre": f"{codigo}-{n}{ext}",
                    "contenido": r.content,
                    "url": url,
                }
    return None


def procesar(articulos, max_fotos, exts, workers):
    """Devuelve (encontrados, sin_marca, sin_fotos)."""
    encontrados = []
    sin_marca = []
    sin_fotos = []

    tareas = []  # (codigo, carpeta, n)
    for codigo in articulos:
        carpeta = detectar_carpeta(codigo)
        if carpeta is None:
            sin_marca.append(codigo)
            continue
        for n in range(1, max_fotos + 1):
            tareas.append((codigo, carpeta, n))

    if not tareas:
        return encontrados, sin_marca, sin_fotos

    session = requests.Session()
    session.headers.update({"User-Agent": "Distrinando-FotoDownloader/1.0"})

    progreso = st.progress(0.0, text="Buscando fotos en Azure...")
    total = len(tareas)
    hechas = 0

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futuros = {
            ex.submit(bajar_una, session, c, carp, n, exts): (c, n)
            for (c, carp, n) in tareas
        }
        for fut in cf.as_completed(futuros):
            res = fut.result()
            if res:
                encontrados.append(res)
            hechas += 1
            progreso.progress(hechas / total, text=f"Buscando fotos... {hechas}/{total}")

    progreso.empty()

    # Articulos con marca valida pero sin ninguna foto
    con_foto = {e["codigo"] for e in encontrados}
    for codigo in articulos:
        if detectar_carpeta(codigo) is not None and codigo not in con_foto:
            sin_fotos.append(codigo)

    # Ordenar por codigo y numero de foto
    encontrados.sort(key=lambda e: (e["codigo"], e["n"]))
    return encontrados, sin_marca, sin_fotos


def armar_zip(encontrados, por_carpeta: bool) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for e in encontrados:
            ruta = f"{e['codigo']}/{e['nombre']}" if por_carpeta else e["nombre"]
            z.writestr(ruta, e["contenido"])
    return buf.getvalue()


# =====================================================================
#  INTERFAZ
# =====================================================================
st.title("📷 Descargar fotos desde Azure")
st.caption(
    "Pega los articulos (SKU / modelo-color), uno por linea. "
    "La app detecta la marca por el prefijo y baja TODAS las fotos "
    "(-1, -2, -3...), aunque haya saltos."
)

col_izq, col_der = st.columns([2, 1])

with col_izq:
    texto = st.text_area(
        "Articulos (uno por linea)",
        height=260,
        placeholder="RBK100033738\nCOL1234ABC\nC205089\nK123456\nP987654",
    )

with col_der:
    st.markdown("**Opciones**")
    max_fotos = st.slider("Maximo de fotos por articulo", 1, 30, 12)
    exts_sel = st.multiselect(
        "Extensiones a probar",
        [".jpg", ".jpeg", ".png", ".webp"],
        default=[".jpg"],
    )
    por_carpeta = st.checkbox("Agrupar el ZIP por articulo", value=True)
    workers = st.slider("Descargas en paralelo", 4, 40, 16)

    st.markdown("**Marcas / prefijos**")
    st.markdown(
        "- `RBK` → Reebok\n"
        "- `COL` → Columbia\n"
        "- `C` → Crocs\n"
        "- `K` → Kappa\n"
        "- `P` → Piccadilly"
    )

buscar = st.button("Buscar y descargar fotos", type="primary", use_container_width=True)

if buscar:
    articulos = [l.strip() for l in texto.splitlines() if l.strip()]
    # Sacar duplicados conservando el orden
    vistos = set()
    articulos = [a for a in articulos if not (a in vistos or vistos.add(a))]

    if not articulos:
        st.warning("Pega al menos un articulo.")
    elif not exts_sel:
        st.warning("Elegi al menos una extension.")
    else:
        encontrados, sin_marca, sin_fotos = procesar(
            articulos, max_fotos, exts_sel, workers
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Articulos", len(articulos))
        c2.metric("Fotos encontradas", len(encontrados))
        c3.metric("Sin fotos", len(sin_fotos))
        c4.metric("Marca no detectada", len(sin_marca))

        if encontrados:
            zip_bytes = armar_zip(encontrados, por_carpeta)
            st.download_button(
                "⬇️ Descargar todas las fotos (.zip)",
                data=zip_bytes,
                file_name="fotos_azure.zip",
                mime="application/zip",
                type="primary",
                use_container_width=True,
            )

            # Resumen por articulo
            resumen = {}
            for e in encontrados:
                resumen.setdefault(e["codigo"], []).append(e["n"])
            with st.expander("Detalle por articulo", expanded=True):
                for codigo, nums in resumen.items():
                    st.write(f"**{codigo}** — {len(nums)} fotos: {sorted(nums)}")

            # Vista previa
            st.subheader("Vista previa")
            cols = st.columns(6)
            for i, e in enumerate(encontrados):
                with cols[i % 6]:
                    st.image(e["contenido"], caption=e["nombre"], use_container_width=True)

        if sin_fotos:
            st.warning("Sin ninguna foto encontrada: " + ", ".join(sin_fotos))
        if sin_marca:
            st.error("Marca no detectada (revisar prefijo): " + ", ".join(sin_marca))
