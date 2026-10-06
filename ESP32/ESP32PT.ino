// =============================
// VNA ESP32 - VERSION FINAL ESTABLE
// =============================

#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <Keypad.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// OLED
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define OLED_ADDR 0x3C
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// WiFi + WS
const char* ssid = "API_LAPTOP_VNA";
const char* password = "Chispaxd";

const char* websocket_host = "192.168.137.1";
const uint16_t websocket_port = 8000;
const char* websocket_path = "/esp32";

WebSocketsClient webSocket;

// IP estática
IPAddress local_IP(192,168,137,200);
IPAddress gateway(192,168,137,1);
IPAddress subnet(255,255,255,0);

// KEYPAD
const byte ROWS = 4, COLS = 4;
char keys[ROWS][COLS] = {
  {'1','2','3','A'}, {'4','5','6','B'},
  {'7','8','9','C'}, {'*','0','#','D'}
};
byte rowPins[ROWS] = {32, 33, 25, 27};
byte colPins[COLS] = {14, 13, 26, 12};
Keypad keypad = Keypad(makeKeymap(keys), rowPins, colPins, ROWS, COLS);

// BOTONES
const uint8_t BTN_SMITH = 23;
const uint8_t BTN_S11 = 19;
const uint8_t BTN_ROE = 18;
const uint8_t BTN_BARRIDO = 15;
const uint8_t BTN_SCREENSHOT = 4;
const uint8_t BTN_EXPORTAR = 5;

struct Button {
  uint8_t pin;
  bool state;
  bool lastState;
  unsigned long lastDebounce;
  const char* nombre;
  const char* evento;
};

Button botones[] = {
  {BTN_SMITH, false, false, 0, "Smith", "mostrar_smith"},
  {BTN_S11, false, false, 0, "S11", "medir_s11"},
  {BTN_ROE, false, false, 0, "ROE", "medir_roe"},
  {BTN_BARRIDO, false, false, 0, "Barrido", "toggle_barrido"},
  {BTN_SCREENSHOT, false, false, 0, "Screenshot", "captura_pantalla"},
  {BTN_EXPORTAR, false, false, 0, "Exportar", "exportar_datos"},
};
const int NUM_BOTONES = 6;
const unsigned long debounceDelay = 50;

// =============================
// ESTADO GLOBAL
// =============================
bool wifiOK = false;
bool wsOK = false;

bool barrido = false;
String fCentro = "---";
String fLateral = "---";
String unidadCentro = "MHz";
String unidadLateral = "MHz";
unsigned long lastHeartbeatMs = 0;
unsigned long lastWsCheckMs = 0;

const unsigned long HEARTBEAT_INTERVAL = 5000;

String lastButtonEvent = "---";
bool editandoCentro = false;
bool editandoLateral = false;

// =============================
// SETUP
// =============================
void setup() {
  Serial.begin(115200);
  Wire.begin(21,22);

  if(!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    while(1);
  }

  renderOLED("Iniciando...");

  // IP fija
  WiFi.config(local_IP, gateway, subnet);
  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    renderOLED("WiFi...");
  }

  wifiOK = true;

  webSocket.begin(websocket_host, websocket_port, websocket_path);
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000);

  for (int i = 0; i < NUM_BOTONES; i++) {
    pinMode(botones[i].pin, INPUT_PULLUP);
    Serial.printf("[BTN INIT] %s -> GPIO %d\n", botones[i].nombre, botones[i].pin);
  }
}

// =============================
// LOOP
// =============================
void loop() {

  webSocket.loop();
  checkWebSocketHealth();

  // Heartbeat para verificar tráfico saliente cada 5 s.
  if (wsOK && (millis() - lastHeartbeatMs >= HEARTBEAT_INTERVAL)) {
    StaticJsonDocument<192> pingDoc;

    pingDoc["type"] = "heartbeat";
    pingDoc["from"] = "esp32";
    pingDoc["ms"] = millis();
    pingDoc["ip"] = WiFi.localIP().toString();
    pingDoc["rssi"] = WiFi.RSSI(); 

    char pingBuffer[192];

    serializeJson(pingDoc, pingBuffer);

    webSocket.sendTXT(pingBuffer);

    Serial.print("[WS TX] ");
    Serial.println(pingBuffer);

    lastHeartbeatMs = millis();
  }

  checkButtons();
  checkKeypad();

  renderOLED("");  // SIEMPRE refresca
}

// =============================
// BOTON
// =============================
void checkButtons() {
  for (int i = 0; i < NUM_BOTONES; i++) {
    bool lectura = !digitalRead(botones[i].pin);

    if (lectura != botones[i].lastState) {
      botones[i].lastDebounce = millis();
    }

    if ((millis() - botones[i].lastDebounce) > debounceDelay) {
      if (lectura != botones[i].state) {
        botones[i].state = lectura;
        if (botones[i].state) {
          handleButtonPress(i);
        }
      }
    }
    botones[i].lastState = lectura;
  }
}

void handleButtonPress(int idx) {
  lastButtonEvent = botones[idx].nombre;
  Serial.printf("[BTN] %s (GPIO %d)\n", botones[idx].nombre, botones[idx].pin);

  if (idx == 3) {
    barrido = !barrido;
    sendEvent(botones[idx].evento, botones[idx].pin, barrido ? "1" : "0");

    if (!barrido) {
      // Al salir de barrido, enviamos configuración actual.
      sendBarridoConfig();
      editandoCentro = false;
      editandoLateral = false;
    }
  } else {
    sendEvent(botones[idx].evento, botones[idx].pin, "1");
  }
}

void sendEvent(const char* type, uint8_t id, const char* value) {
  StaticJsonDocument<256> doc;
  doc["type"] = type;
  doc["id"] = id;
  doc["value"] = value;

  char buffer[256];
  serializeJson(doc, buffer);
  webSocket.sendTXT(buffer);
  Serial.print("[WS TX] ");
  Serial.println(buffer);
}

void sendBarridoConfig() {
  StaticJsonDocument<384> doc;
  doc["type"] = "config_barrido";
  doc["frecuencia_centro"] = (fCentro == "---") ? "" : fCentro;
  doc["frecuencia_lateral"] = (fLateral == "---") ? "" : fLateral;
  doc["unidad_centro"] = unidadCentro;
  doc["unidad_lateral"] = unidadLateral;
  // Compatibilidad con clientes que aún leen un solo campo.
  doc["unidad"] = (unidadCentro == unidadLateral) ? unidadCentro : "MIX";

  char buffer[384];
  serializeJson(doc, buffer);
  webSocket.sendTXT(buffer);
  Serial.print("[WS TX] ");
  Serial.println(buffer);
}

void sendESP32Online() {

  StaticJsonDocument<256> doc;

  doc["client"] = "esp32";
  doc["type"] = "esp32_online";
  doc["ip"] = WiFi.localIP().toString();
  doc["rssi"] = WiFi.RSSI();

  char buffer[256];

  serializeJson(doc, buffer);

  webSocket.sendTXT(buffer);

  Serial.print("[WS TX] ");
  Serial.println(buffer);
}

void checkKeypad() {
  char key = keypad.getKey();
  if (!key) {
    return;
  }

  Serial.printf("[KEYPAD] %c | Barrido:%s\n", key, barrido ? "ON" : "OFF");

  // D = reconexión forzada WS.
  if (key == 'D') {
    if (!webSocket.isConnected()) {
      Serial.println("[KEYPAD] Reconnect WS");
      webSocket.begin(websocket_host, websocket_port, websocket_path);
    } else {
      Serial.println("[KEYPAD] WS ya conectado");
    }
    return;
  }

  // Solo aceptar configuración de keypad cuando barrido está activo.
  if (!barrido) {
    return;
  }

  switch (key) {
    case 'A':
      if (editandoCentro) {
        unidadCentro = "kHz";
        lastButtonEvent = "Centro kHz";
      } else if (editandoLateral) {
        unidadLateral = "kHz";
        lastButtonEvent = "Lateral kHz";
      } else {
        unidadCentro = "kHz";
        unidadLateral = "kHz";
        lastButtonEvent = "Unidad kHz";
      }
      sendBarridoConfig();
      break;
    case 'B':
      if (editandoCentro) {
        unidadCentro = "MHz";
        lastButtonEvent = "Centro MHz";
      } else if (editandoLateral) {
        unidadLateral = "MHz";
        lastButtonEvent = "Lateral MHz";
      } else {
        unidadCentro = "MHz";
        unidadLateral = "MHz";
        lastButtonEvent = "Unidad MHz";
      }
      sendBarridoConfig();
      break;
    case 'C':
      if (editandoCentro) {
        unidadCentro = "GHz";
        lastButtonEvent = "Centro GHz";
      } else if (editandoLateral) {
        unidadLateral = "GHz";
        lastButtonEvent = "Lateral GHz";
      } else {
        unidadCentro = "GHz";
        unidadLateral = "GHz";
        lastButtonEvent = "Unidad GHz";
      }
      sendBarridoConfig();
      break;
    case '*':
      editandoCentro = true;
      editandoLateral = false;
      fCentro = "";
      lastButtonEvent = "Edit Centro";
      break;
    case '#':
      editandoLateral = true;
      editandoCentro = false;
      fLateral = "";
      lastButtonEvent = "Edit Lateral";
      break;
    default:
      if (key >= '0' && key <= '9') {
        if (editandoCentro) {
          fCentro += key;
          lastButtonEvent = "Centro " + fCentro;
          sendBarridoConfig();
        } else if (editandoLateral) {
          fLateral += key;
          lastButtonEvent = "Lateral " + fLateral;
          sendBarridoConfig();
        }
      }
      break;
  }

}

void checkWebSocketHealth() {

    static unsigned long lastReconnectAttempt = 0;

    // Sin WiFi no tiene sentido intentar WS
    if (WiFi.status() != WL_CONNECTED) {
        return;
    }

    // Si ya está conectado no hacer nada
    if (webSocket.isConnected()) {
        return;
    }

    unsigned long now = millis();

    // Evitar spam de reconexión
    if (now - lastReconnectAttempt < 5000) {
        return;
    }

    lastReconnectAttempt = now;

    Serial.println("[WS] reconnecting...");

    webSocket.begin(
        websocket_host,
        websocket_port,
        websocket_path
    );

    webSocket.onEvent(webSocketEvent);

    webSocket.setReconnectInterval(5000);
}

// =============================
// WS EVENTOS
// =============================
void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {

  switch(type) {

    case WStype_CONNECTED:
      Serial.println("[WS] connected");
      wsOK = true;
      sendESP32Online();
      break;

    case WStype_DISCONNECTED:
      Serial.println("[WS] disconnected");
      wsOK = false;
      break;

    case WStype_TEXT:
      Serial.print("[WS RX] ");
      Serial.println((char*)payload);
      procesarJSON(payload);
      break;

    case WStype_ERROR:
      wsOK = false;
      Serial.println("[WS] Error");
      break;

    default:
      break;
  }
}

// =============================
// PROCESAR JSON
// =============================
void procesarJSON(uint8_t * payload) {

  StaticJsonDocument<2048> doc;
  DeserializationError err = deserializeJson(doc, payload);
  if (err) {
    Serial.print("[JSON] Error parse: ");
    Serial.println(err.c_str());
    return;
  }

  if (doc["type"] == "update") {

    barrido = doc["state"]["barrido"] | barrido;
    fCentro = doc["state"]["frecuencia_centro"] | fCentro;
    fLateral = doc["state"]["frecuencia_lateral"] | fLateral;
    unidadCentro = doc["state"]["unidad_centro"] | (doc["state"]["unidad"] | unidadCentro);
    unidadLateral = doc["state"]["unidad_lateral"] | (doc["state"]["unidad"] | unidadLateral);
  } else if (doc["type"] == "error") {
    const char* msg = doc["message"] | "error";
    Serial.print("[API ERROR] ");
    Serial.println(msg);
  }
}

// =============================
// OLED CENTRAL
// =============================
void renderOLED(String extra) {

  display.clearDisplay();
  display.setCursor(0,0);
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  display.println("VNA ESP32");

  display.print("IP:");
  display.println(WiFi.localIP());

  display.print("WiFi: ");
  display.println(wifiOK ? "OK" : "...");

  display.print("WS: ");
  display.println(wsOK ? "OK" : "...");

  display.print("RSSI: ");
  display.println(WiFi.RSSI());

  display.println("");

  display.print("Barrido: ");
  display.println(barrido ? "ON" : "OFF");

  display.print("C: ");
  display.print(fCentro);
  display.print(" ");
  display.println(unidadCentro);

  display.print("L: ");
  display.print(fLateral);
  display.print(" ");
  display.println(unidadLateral);

  display.print("Evt: ");
  display.println(lastButtonEvent);

  if(extra != "") {
    display.println("");
    display.println(extra);
  }

  display.display();
}