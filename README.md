# Proyecto Terminal
Códigos desarrollados para el Proyecto Terminal: "Laboratorio virtual para el uso de un analizador vectorial de redes" dentro de la Unidad Profesional Interdisciplinaria en Ingeniería y Tecnologías Avanzadas.

## Descarga de APK para Metaquest

Dentro de la siguiente liga se encuentra el APK para las Metaquest 3S

[Descarga de APK](https://drive.google.com/file/d/1DMBeIVCOr3HpQgJZRVssmDR-YFnOCSIo/view?usp=sharing)

descargar e instalar directamente en el visor.

## Configuración en una PC nueva

Sigue estos pasos para dejar el proyecto listo para ejecutarse en una computadora nueva.

### 1. Configurar MongoDB

1. Instala y ejecuta MongoDB en la computadora donde correrá el proyecto.
2. Verifica que la URI de conexión sea la correcta para esa instalación. Por defecto, los scripts del proyecto usan `mongodb://localhost:27017`.
3. Si MongoDB no está en la misma máquina, actualiza la URI en los archivos que se conectan a la base de datos:
	- [Python/JSONtoMongo_2.py](Python/JSONtoMongo_2.py)
	- [Python/API/APIFINAL.py](Python/API/APIFINAL.py)

### 2. Cargar los datasets en MongoDB

En la carpeta [Data](Data) ya están los 4 archivos JSON necesarios, así que no hace falta usar el conversor de CSV a JSON.

Debes cargar cada uno de estos datasets en la base de datos de MongoDB configurada en la PC:

- [Data/dataDipoloCobre.json](Data/dataDipoloCobre.json)
- [Data/dataDipoloPlata.json](Data/dataDipoloPlata.json)
- [Data/dataMonopoloCobre.json](Data/dataMonopoloCobre.json)
- [Data/dataMonopoloSilver.json](Data/dataMonopoloSilver.json)

Para hacerlo, usa el script de Python encargado de subir los JSON a MongoDB. Como ese script busca los archivos por ruta relativa, ejecútalo desde el directorio donde estén disponibles los JSON o ajusta las rutas del arreglo `ARCHIVOS` para que apunten a la carpeta [Data](Data). El script valida los documentos e inserta o actualiza los registros en la colección correspondiente.

### 3. Configurar el hotspot para el ESP32

Antes de iniciar el ESP32, crea un hotspot desde la laptop o PC con estas credenciales:

- Nombre de red: `API_LAPTOP_VNA`
- Contraseña: `Chispaxd`

Estas credenciales deben coincidir con las que tenga configuradas el ESP32 para que pueda conectarse correctamente. Si es necesario, modifica el código de conexión Wi-Fi en [ESP32/ESP32PT.ino](ESP32/ESP32PT.ino).

### 4. Iniciar la API

Antes de levantar el servicio, asegúrate de permitir el acceso al puerto `8000` en el firewall de Windows. Debes crear una regla de entrada y otra de salida que permitan tráfico tanto `TCP` como `UDP` para ese puerto.

También es necesario cambiar el tipo de perfil de red de la conexión que usarás con la API a **Red Privada**, desde la configuración de red de Windows, para evitar bloqueos de descubrimiento y conectividad en la red local.

Una vez cargados los datos y configurado el hotspot, inicia la API desde la carpeta [Python/API](Python/API) con este comando:

```bash
python -m uvicorn APIFINAL:app --reload --reload-dir . --host 0.0.0.0 --port 8000
```

Con esto la API quedará escuchando en el puerto `8000` y será accesible desde otros dispositivos en la misma red.
