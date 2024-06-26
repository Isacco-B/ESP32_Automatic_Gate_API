from umqtt.simple import MQTTClient
from utils.utils import validate_data, send_data_i2c, test_i2c_connection, connect_to_wifi, is_wifi_connected
import machine
import ubinascii
import micropython
import time
import json
import asyncio
from asyncio import Event
from secrets import SERVER, USER, PASSWORD

# Constants
I2C_TIMEOUT = 5
NOTIFICATION_TIMEOUT = 60
SLEEP_INTERVAL = 0.2
MQTT_RETRY_INTERVAL = 1

# Topic
TOPICS = {
    "GATE": b"api/gate",
    "PARTIAL_GATE": b"api/gate/partial",
    "SMALL_GATE": b"api/small_gate",
    "GARAGE_LIGHT": b"api/garage/light",
    "GET_GATE_STATUS": b"api/gate/get_status",
}

# PinOut
small_gate = machine.Pin(12, machine.Pin.OUT)
garage_light = machine.Pin(14, machine.Pin.OUT)

# Initialize
test_i2c_connection()
connect_to_wifi()

# State variables
garage_light_event = Event()
small_gate_event = Event()
gate_status_event = Event()

# Initialize events as set to allow first execution
garage_light_event.set()
small_gate_event.set()
gate_status_event.set()

async def toggle_garage_light():
    """Toggle the garage light based on the message."""
    if not garage_light_event.is_set():
        print("Garage light is busy. Ignoring command.")
        return
    garage_light_event.clear()
    garage_light.on()
    await asyncio.sleep(1)
    garage_light.off()
    await asyncio.sleep(0.2)
    garage_light_event.set()
        
async def toggle_small_gate():
    """Toggle the small gate based on the message."""
    if not small_gate_event.is_set():
        print("Small gate is busy. Ignoring command.")
        return
    small_gate_event.clear()
    small_gate.on()
    await asyncio.sleep(1)
    small_gate.off()
    await asyncio.sleep(0.2)
    small_gate_event.set()
        
async def send_notification(client, topic, message):
    """Send a notification to the specified MQTT topic."""
    if client is not None:
        client.publish(topic, message)
    
async def handle_message(topic, msg, client):
    """Handle incoming MQTT messages and take appropriate action."""
    print((topic, msg))
    try:
        
        if topic == TOPICS["GATE"]:
            if msg == b"on":
                await process_gate_command(client, b"1", "gate")
                
        elif topic == TOPICS["PARTIAL_GATE"]:
            if msg == b"on":
                await process_gate_command(client, b"2", "gate/partial")
                
        elif topic == TOPICS["SMALL_GATE"]:
            if msg == b"on":
                response = {"data": "Cancellino: Eseguito con successo"}
                await send_notification(client, b"api/notification/small_gate", json.dumps(response))
                await toggle_small_gate()
                
            
        elif topic == TOPICS["GARAGE_LIGHT"]:
            if msg == b"on":
                response = {"data": "Luce Garage: Eseguito con successo"}
                await send_notification(client, b"api/notification/garage/light", json.dumps(response))
                await toggle_garage_light()
 
            
        elif topic == TOPICS["GET_GATE_STATUS"]:
            if msg == b"on":
                await send_gate_status(client)
            
    except Exception as e:
        print(f"Error handling message {topic}: {e}")
        
async def process_gate_command(client, command, notification_suffix):
    """Process a gate command and send a notification with the result."""
    data = await send_data_i2c(command, timeout=I2C_TIMEOUT, response_byte=2)
    if 'err' in data:
        print(data)
        return
    else:
        response = {"data": "Pedonabile: Eseguito con successo"}
        if notification_suffix == "gate":
            response = {"data": "Cancello: Eseguito con successo"}
        await send_notification(client, f"api/notification/{notification_suffix}", json.dumps(response))
        
async def send_gate_status(client):
    """Send the current gate status to the MQTT topic."""
    start_time = time.time()
    if not gate_status_event.is_set():
        print("Gate Status is busy. Ignoring command.")
        return
    gate_status_event.clear()
    try:
        while True:
            if time.time() - start_time > NOTIFICATION_TIMEOUT:
                break
            data = await send_data_i2c(b"3", timeout=I2C_TIMEOUT, response_byte=20)
            print(data)
            if 'err' in data:
                continue
            status_json = process_gate_status(data)
            if status_json:
                await send_notification(client, b"api/notification/gate/status", status_json)
            await asyncio.sleep(SLEEP_INTERVAL)
    finally:
        await asyncio.sleep(0.2)
        gate_status_event.set()
                 
def process_gate_status(data):
    """Process gate status data and return as JSON."""
    decoded_string = data["data"].decode("utf8")
    status_parts = decoded_string.split(',')

    if not validate_data(status_parts):
        print("Invalid status data!")
        return None

    state_translation = {"0": "chiuso", "1": "aperto", "2": "stop", "3": "in apertura", "4": "in chiusura"}
    option_translation = {"0": "disattivo", "1": "attivo"}
    
    if status_parts[1][0] == "0":
        status_parts[1] = status_parts[1][1:]

    status_dict = {
        "stato": state_translation.get(status_parts[0], "sconosciuto"),
        "posizione": status_parts[1],
        "fcApertura": option_translation.get(status_parts[2], "sconosciuto"),
        "fcChiusura": option_translation.get(status_parts[3], "sconosciuto"),
        "fotocellule": option_translation.get(status_parts[4], "sconosciuto"),
        "coste": option_translation.get(status_parts[5], "sconosciuto"),
        "consumo": status_parts[6],
        "ricevente": option_translation.get(status_parts[7], "sconosciuto")
    }
    return json.dumps(status_dict)
    
def sub_cb_closure(client):
    """Closure to handle subscription callback with asyncio."""
    def sub_cb(topic, msg):
        asyncio.create_task(handle_message(topic, msg, client))
    return sub_cb

def connect_to_mqtt():
    """Connect to the MQTT server and handle reconnection attempts."""
    if not is_wifi_connected():
        connect_to_wifi()
        
    while True:
        client = MQTTClient(client_id=ubinascii.hexlify(machine.unique_id()), server=SERVER, user=USER, password=PASSWORD)
        client.set_callback(sub_cb_closure(client))
        try:
            client.connect()
            time.sleep(2)
            for topic in TOPICS.values():
                client.subscribe(topic)
            print(f"Connected to {SERVER}")
            return client
        except OSError as e:
            print(f"Connection failed: {e}. Retrying...")
            time.sleep(MQTT_RETRY_INTERVAL)

    
async def keep_connection_active(client):
    while True:
        if is_wifi_connected() and client is not None:
            try:
                print("Ping send to Broker...")
                client.publish("api/ping", "ping")
                await asyncio.sleep(10)
            except Exception as e:
                print(f"Error sending ping to broker: {e}")
                break
        
async def main():
    """Main entry point for the asyncio loop."""
    while True:
        try:
            client = connect_to_mqtt()
            keep_connection_task = asyncio.create_task(keep_connection_active(client))
            while True:
                await asyncio.sleep(0.2)
                try:
                    client.check_msg()
                except OSError as e:
                    print(f"Error checking messages: {e}")
                    break
        except OSError as e:
            print(f"MQTT communication error: {e}")
        finally:
            try:
                client.disconnect()
            except OSError as e:
                print(f"Error disconnecting client: {e}")
            if keep_connection_task:
                keep_connection_task.cancel()
                try:
                    await keep_connection_task
                except asyncio.CancelledError:
                    print("keep_connection_task has been cancelled")
            time.sleep(MQTT_RETRY_INTERVAL)
                
asyncio.run(main())