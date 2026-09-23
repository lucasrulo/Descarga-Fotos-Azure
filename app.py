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
#                 https://<dominio>/cdn/shop/files/<sku>-<n>.jpgimport io
import re
import zipfile
import concurrent.futures as cf

import requests
import streamlit as st

# =====================================================================
#  Descargador de fotos de producto (Distrinando)
#
#  Dos fuentes:
#   1) AZURE   -> blob del catalogo interno. No tiene API, asi que se
#                 arma la URL y se prueban los sufijos -1, -2, -3...
#                 https://distriecomm.blob.core.windows.net/catalogo/<Marca>/<sku>-<n>.jpg
#   2) SHOPIFY -> se le PREGUNTA a la tienda por el SKU (search + product
#                 JSON) y se bajan las URLs reales de todas las fotos.
#                 Asi funcionan tambien los casos que no se pueden adivinar
#                 (nombres con hash, con letras distintas al SKU, etc.).
# =====================================================================

st.set_page_config(page_title="Descargar fotos", page_icon="📷", layout="wide")

# ---------------------------------------------------------------------
#  Marcas: prefijo -> nombre. El ORDEN importa (primero los de 3
#  letras: RBK, COL; despues los de 1: C, K, P).
# ---------------------------------------------------------------------
MARCAS = [
    ("RBK", "Reebok"),
    ("COL", "Columbia"),
    ("C",   "Crocs"),
    ("K",   "Kappa"),
    ("P",   "Piccadilly"),
]

# ---- AZURE ----
AZURE_BASE = "https://distriecomm.blob.core.windows.net/catalogo/"
AZURE_CARPETA = {
    "Reebok": "Reebok",
    "Columbia": "Columbia",
    "Crocs": "Crocs",
    "Kappa": "Kappa",
    "Piccadilly": "Piccadilly",
}

# ---- SHOPIFY ----  dominios de las tiendas (editables en la UI)
SHOPIFY_DOMINIOS_DEFAULT = {
    "Reebok": "reebok.com.ar",
    "Columbia": "columbiasportswear.com.ar",
    "Crocs": "www.crocs.com.ar",
    "Kappa": "www.kappastore.com.ar",
    "Piccadilly": "www.piccadilly.com.ar",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FotoDownloader/2.0",
    "Accept": "application/json,text/plain,*/*",
}


def detectar_marca(codigo: str):
    up = codigo.strip().upper()
    for prefijo, marca in MARCAS:
        if up.startswith(prefijo):
            return marca
    return None


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# =====================================================================
#  AZURE: adivinar sufijos -1, -2, -3...
# =====================================================================
def azure_bajar_una(session, codigo, marca, n, exts):
    carpeta = AZURE_CARPETA.get(marca)
    if not carpeta:
        return None
    for ext in exts:
        url = f"{AZURE_BASE}{carpeta}/{codigo}-{n}{ext}"
        try:
            r = session.get(url, timeout=20)
        except requests.RequestException:
            continue
        if r.status_code == 200 and r.content and len(r.content) > 100:
            ct = r.headers.get("Content-Type", "")
            if ct.startswith("image") or not ct.startswith(("text", "application/xml")):
                return {"codigo": codigo, "n": n, "nombre": f"{codigo}-{n}{ext}", "contenido": r.content}
    return None


def azure_procesar(articulos, max_fotos, exts, workers):
    encontrados, sin_marca, sin_fotos = [], [], []
    tareas = []
    for codigo in articulos:
        marca = detectar_marca(codigo)
        if marca is None:
            sin_marca.append(codigo)
            continue
        for n in range(1, max_fotos + 1):
            tareas.append((codigo, marca, n))

    if not tareas:
        return encontrados, sin_marca, sin_fotos

    session = requests.Session()
    session.headers.update(HEADERS)
    progreso = st.progress(0.0, text="Buscando fotos en Azure...")
    total, hechas = len(tareas), 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futuros = [ex.submit(azure_bajar_una, session, c, m, n, exts) for (c, m, n) in tareas]
        for fut in cf.as_completed(futuros):
            res = fut.result()
            if res:
                encontrados.append(res)
            hechas += 1
            progreso.progress(hechas / total, text=f"Buscando fotos... {hechas}/{total}")
    progreso.empty()

    con_foto = {e["codigo"] for e in encontrados}
    for codigo in articulos:
        if detectar_marca(codigo) is not None and codigo not in con_foto:
            sin_fotos.append(codigo)
    encontrados.sort(key=lambda e: (e["codigo"], e["n"]))
    return encontrados, sin_marca, sin_fotos


# =====================================================================
#  SHOPIFY: preguntarle a la tienda por el SKU y bajar las URLs reales
# =====================================================================
def _fix_url(u: str) -> str:
    if u.startswith("//"):
        return "https:" + u
    return u


def _basename_ext(url: str) -> str:
    path = url.split("?")[0]
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ".jpg"
    if len(ext) > 6 or "/" in ext:
        ext = ".jpg"
    return ext


def shopify_buscar_handle(session, dom, sku):
    """Devuelve el handle del producto que corresponde al SKU."""
    target = norm(sku.split("/")[0])  # el sufijo de color tipo /B no va en el archivo
    try:
        r = session.get(
            f"https://{dom}/search/suggest.json",
            params={
                "q": sku,
                "resources[type]": "product",
                "resources[limit]": "10",
            },
            timeout=20,
        )
        data = r.json()
    except Exception:
        return None

    productos = (
        data.get("resources", {}).get("results", {}).get("products", [])
        if isinstance(data, dict) else []
    )
    candidatos = []
    for p in productos:
        h = p.get("handle") or (p.get("url", "").split("/products/")[-1].split("?")[0])
        if h:
            candidatos.append(h)

    # 1) match directo por handle
    for h in candidatos:
        if target and target in norm(h):
            return h

    # 2) fallback: revisar los SKU de variantes de cada candidato
    for h in candidatos:
        imgs, skus = shopify_producto_js(session, dom, h)
        if any(target and target in norm(s) for s in skus):
            return h

    return candidatos[0] if candidatos else None


def shopify_producto_js(session, dom, handle):
    """Devuelve (lista_de_urls_de_imagenes, lista_de_skus) del producto."""
    try:
        r = session.get(f"https://{dom}/products/{handle}.js", timeout=20)
        data = r.json()
    except Exception:
        return [], []
    imgs = [_fix_url(u) for u in data.get("images", []) if u]
    skus = [v.get("sku", "") for v in data.get("variants", [])]
    return imgs, skus


def shopify_procesar_sku(dom, sku):
    """Devuelve lista de fotos {codigo, n, nombre, contenido} para un SKU."""
    session = requests.Session()
    session.headers.update(HEADERS)
    handle = shopify_buscar_handle(session, dom, sku)
    if not handle:
        return []
    imgs, _ = shopify_producto_js(session, dom, handle)
    fotos = []
    safe = sku.replace("/", "-")
    for i, url in enumerate(imgs, start=1):
        try:
            r = session.get(url, timeout=25)
        except requests.RequestException:
            continue
        if r.status_code == 200 and r.content and len(r.content) > 100:
            ext = _basename_ext(url)
            fotos.append({
                "codigo": sku,
                "n": i,
                "nombre": f"{safe}-{i}{ext}",
                "contenido": r.content,
            })
    return fotos


def shopify_procesar(articulos, dominios, workers):
    encontrados, sin_marca, sin_fotos = [], [], []
    trabajo = []  # (sku, dominio)
    for sku in articulos:
        marca = detectar_marca(sku)
        dom = (dominios.get(marca) or "").strip().rstrip("/").replace("https://", "").replace("http://", "")
        if marca is None or not dom:
            sin_marca.append(sku)
            continue
        trabajo.append((sku, dom))

    if not trabajo:
        return encontrados, sin_marca, sin_fotos

    progreso = st.progress(0.0, text="Consultando las tiendas...")
    total, hechas = len(trabajo), 0
    with cf.ThreadPoolExecutor(max_workers=min(workers, 10)) as ex:
        futuros = {ex.submit(shopify_procesar_sku, dom, sku): sku for (sku, dom) in trabajo}
        for fut in cf.as_completed(futuros):
            sku = futuros[fut]
            fotos = fut.result()
            if fotos:
                encontrados.extend(fotos)
            else:
                sin_fotos.append(sku)
            hechas += 1
            progreso.progress(hechas / total, text=f"Consultando tiendas... {hechas}/{total}")
    progreso.empty()

    encontrados.sort(key=lambda e: (e["codigo"], e["n"]))
    return encontrados, sin_marca, sin_fotos


# =====================================================================
def armar_zip(encontrados, por_carpeta):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for e in encontrados:
            ruta = f"{e['codigo'].replace('/', '-')}/{e['nombre']}" if por_carpeta else e["nombre"]
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
    help="Azure = blob del catalogo interno. Shopify = fotos cargadas en las tiendas online.",
)

if fuente == "Shopify":
    st.caption(
        "Pega los SKU (uno por linea). Le pregunta a cada tienda por el SKU y baja "
        "TODAS las fotos reales que tenga cargadas (incluye nombres con hash que no se pueden adivinar)."
    )
else:
    st.caption(
        "Pega los SKU (uno por linea). Detecta la marca por el prefijo y baja "
        "las fotos -1, -2, -3... del blob (aunque haya saltos)."
    )

col_izq, col_der = st.columns([2, 1])

with col_izq:
    texto = st.text_area(
        "SKU (uno por linea)",
        height=260,
        placeholder="K2382M2CWZ-KD00\nK532287UW-KF64/B\nC211355-C0WV\nRBK100033738",
    )

with col_der:
    st.markdown("**Opciones**")
    if fuente == "Azure":
        max_fotos = st.slider("Maximo de fotos por SKU", 1, 30, 12)
        exts_sel = st.multiselect(
            "Extensiones a probar", [".jpg", ".jpeg", ".png", ".webp"], default=[".jpg"]
        )
    else:
        max_fotos, exts_sel = 0, []
        st.info("En Shopify baja automaticamente todas las fotos que tenga el producto.")
    workers = st.slider("Descargas en paralelo", 4, 40, 12)

# Dominios de tiendas (solo Shopify)
dominios = dict(SHOPIFY_DOMINIOS_DEFAULT)
if fuente == "Shopify":
    with st.expander("🌐 Dominios de las tiendas Shopify"):
        st.caption("Solo el dominio, sin https:// (ej: www.crocs.com.ar).")
        cols = st.columns(3)
        for i, marca in enumerate(["Crocs", "Reebok", "Columbia", "Kappa", "Piccadilly"]):
            dominios[marca] = cols[i % 3].text_input(marca, value=SHOPIFY_DOMINIOS_DEFAULT[marca])

agrupacion = st.radio(
    "¿Cómo querés el ZIP?",
    ["Una carpeta por artículo", "Todas las fotos juntas (sin carpetas)"],
    horizontal=True,
)
por_carpeta = agrupacion.startswith("Una carpeta")

buscar = st.button("Buscar fotos", type="primary", use_container_width=True)

# ---------------------------------------------------------------------
if buscar:
    articulos = [l.strip() for l in texto.splitlines() if l.strip()]
    vistos = set()
    articulos = [a for a in articulos if not (a in vistos or vistos.add(a))]

    if not articulos:
        st.warning("Pega al menos un SKU.")
        st.session_state.pop("resultado", None)
    elif fuente == "Azure" and not exts_sel:
        st.warning("Elegi al menos una extension.")
        st.session_state.pop("resultado", None)
    else:
        if fuente == "Azure":
            encontrados, sin_marca, sin_fotos = azure_procesar(articulos, max_fotos, exts_sel, workers)
        else:
            encontrados, sin_marca, sin_fotos = shopify_procesar(articulos, dominios, workers)
        st.session_state["resultado"] = {
            "articulos": articulos,
            "encontrados": encontrados,
            "sin_marca": sin_marca,
            "sin_fotos": sin_fotos,
        }

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
                st.write(f"**{codigo}** — {len(nums)} fotos")

        st.subheader("Vista previa")
        cols = st.columns(6)
        for i, e in enumerate(encontrados):
            with cols[i % 6]:
                st.image(e["contenido"], caption=e["nombre"], use_container_width=True)

    if sin_fotos:
        st.warning("Sin fotos encontradas: " + ", ".join(sin_fotos))
    if sin_marca:
        st.error("Sin dominio cargado o marca no detectada: " + ", ".join(sin_marca))
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
        # El CDN de Shopify distingue mayus/minus. Cada tienda guarda el
        # archivo con una convencion distinta (Crocs mayus, Kappa minus),
        # asi que probamos el codigo tal cual, en minuscula y en mayuscula.
        casings = list(dict.fromkeys([codigo, codigo.lower(), codigo.upper()]))
        urls = []
        for path in SHOPIFY_PATHS:
            for cod in casings:
                urls.append(f"https://{dom}{path}{cod}-{n}{ext}")
        return urls


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
