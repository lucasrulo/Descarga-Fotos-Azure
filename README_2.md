# Descargar fotos desde Azure (Distrinando)

App en Streamlit que replica la logica de la macro `InsertarFotosDesdeAzure`,
pero en vez de insertar solo la foto `-1`, **descarga todas las fotos** de cada
articulo (`-1`, `-2`, `-3`, `-4`...), sean correlativas o con saltos.

Pegas los articulos (uno por linea), la app detecta la marca por el prefijo,
busca las fotos en el blob de Azure y te arma un `.zip` con todo.

## Marcas / prefijos

| Prefijo | Carpeta en Azure |
|---------|------------------|
| `RBK`   | Reebok           |
| `COL`   | Columbia         |
| `C`     | Crocs            |
| `K`     | Kappa            |
| `P`     | Piccadilly       |

URL base: `https://distriecomm.blob.core.windows.net/catalogo/<Marca>/<codigo>-<n>.jpg`

## Como funciona la busqueda de "todas" las fotos

No sabemos cuantas fotos tiene cada articulo, asi que la app prueba
`-1` hasta `-N` (N = "Maximo de fotos por articulo", por defecto 12) y se queda
con las que existen (HTTP 200). Como prueba todo el rango, tolera saltos
(ej: existe `-1`, `-3` y `-4` pero no `-2`). Las descargas van en paralelo para
que sea rapido. Si algun articulo tiene mas fotos que el maximo, subi el slider.

## Correr localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy en Streamlit Community Cloud (streamlit.io)

1. Crea un repo en GitHub (ej: `descargar-fotos-azure`) y subi estos 3 archivos:
   `app.py`, `requirements.txt`, `README.md`.

   ```bash
   git init
   git add .
   git commit -m "App descargar fotos Azure"
   git branch -M main
   git remote add origin https://github.com/<tu-usuario>/descargar-fotos-azure.git
   git push -u origin main
   ```

2. Entra a https://share.streamlit.io  → **New app**.
3. Elegi el repo, branch `main` y archivo `app.py`.
4. **Deploy**. Listo, queda con una URL publica que podes compartir.

> Nota: las fotos se leen del blob publico de Azure. Si en algun momento el
> contenedor deja de ser publico, habria que agregar un token SAS (te lo
> agrego cuando haga falta).
