# UniPi MQTT

This is a script that creates MQTT messages based on the events that happen on the UniPi device and switches UniPi outputs based on received MQTT messages. Worked with a Unipi 513 here. Main goal is to get MQTT messages to/from Home Assistant.

The script creates a websocket connection to EVOK and based on those websocket messages and a config file MQTT messages are published. 

Also creates a MQTT listener to listen to incoming MQTT topics and switch UniPi outputs based on those messages. I created the system in such a way that info to switch an output must be in the MQTT message. See the Hass examples below. 

WARNING: I am not a programmer, so this code kinda works, but it ain't pretty ;-) (I think...). So there is a big chance you need to tinker a bit in the scripts. It's also a version 1 that I build specifically for my home assistant setup, so it's quite tailored to my personal need and way of working.

Update July 2020, I have a 'UniPi friend' in Belgium now that has a setup too, and changed this to be a bit more generic, so perhaps a bit broader applicable. 

Be sure to use the 2 python scripts (python3) and the json config file. 

## Setup

I put the files in a directory and create a service to automatically start and stop the service on system start. 

Prerequisites:
 - A UniPi system with EVOK (opensource software from EVOK, works both on a self build as wel as on a UniPi provided Evok image from here (https://kb.unipi.technology/en:files:software:os-images:00-start). Don't forget to install Evok afterwards per documentation in that site.
 - MQTT Broker somewhere
 
Setup:
 - Install python 3 and pip (sudo apt install `python3-pip`) 
 - Install the required python packages with pip (`pip3 install paho-mqtt threaded websocket-client statistics requests`)
 - Copy the 3 scripts into a dir (I use /scripts in my UniPi user dir, can be anything)
 - Adjust the vars in the script to your needs, like IP, etc.
 - Adjust the unipi_mqtt_config.json file to correspond to your UniPi and the connected devices to it (see below for more details)
 - optional; Create a service based on this script (example to do so here; https://github.com/MydKnight/PiClasses/wiki/Making-a-script-run-as-daemon-on-boot) Example file in this github (unipi_mqtt.service)
 - Start the service or script and see what happens (sudo service start unipi_mqtt)
 - Logging goes to /var/log/unipi_mqtt.log

## UniPi unipi_mqtt_config.json

A config file is used to describe the inputs on the UniPi so the script knows what to send out when a change on an input is detected. An example config file is in the repo, below you'll find an example entry. It's JSON, so make sure it's valid. 

### Digital In as binary sensor in HA

Example PIR sensor for motion detection in unipi_mqtt_config.json:
```json
   {
      "circuit":"1_04",
      "description":"Kantoor PIR",
      "dev":"input",
      "device_delay":120,
      "device_normal":"no",
      "unipi_value":0,
      "unipi_prev_value":0,
      "unipi_prev_value_timstamp":0,
      "state_topic": "unipi/bgg/kantoor/motion"
   },
```

The HA part for this is a binary sensor: 
```
- platform: mqtt
  name: "Kantoor Motion"
  unique_id: "kantoor_motion"
  state_topic: "unipi/bgg/kantoor/motion"
  payload_on: "ON"
  payload_off: "OFF"
  availability_topic: "unipi/bgg/kantoor/motion/available"
  payload_available: "online"
  payload_not_available: "offline"
  qos: 0
  device_class: presence
```

### Handle local options (3 types)
Example unipi_mqtt_config.json with "handle local" function to handle a local "critical" function within the script so it works without HA or other MQTT connections. This also sends MQTT messages to inform HA about the state change.

#### 1 - Handle Local Bel (on AND off switch of relay in 1 action)
This example rings a bel 3 time (or switches a realy 3 x on and off, so 6 total actions). The bel I use is a Friendland bel on 12 or 24 volt. It rings once on power on and power off (power on pulls the ring stick, power off launches it to ring quite loud). 

```json
   {
      "circuit":"2_05",
      "description":"Voordeur Beldrukker",
      "dev":"input",
      "handle_local":
            {
	            "type": "bel",
				"trigger":"on",
				"rings": 3,
				"output_dev": "output",
				"output_circuit": "2_01"
			},
      "device_delay":1,
      "device_normal":"no",
      "unipi_value":0,
      "unipi_prev_value":0,
      "unipi_prev_value_timstamp":0,
      "state_topic": "unipi/bgg/voordeur/beldrukker"
   }
```

I trigger this in HA from a automation where the action part is;
```
  action:
    - service: mqtt.publish
      data:
        topic: 'homeassistant/bgg/hal/bel/set'
        payload: '{"circuit": "2_01", "dev": "relay", "repeat": "1", "state": "pulse"}'
```

#### 2 - Handle Local Light Dimmer (Analog output 0-10 volt).
Example unipi_mqtt_config.json of a handle local switch with dimmer (analog output 0-10 volt is used to dim an LED source). I use 0-10 volt (not 1-10!) led dimmers. Works flawlessly. Note that the Level in unipi = 0-10 and in HA 0-255 for 0-100%. Not that handle local sets a value, but it's static. Things like holding the sensor to dim are not implemented.

unipi_mqtt_config.json:

```{
      "circuit":"3_02",
      "description":"Schakelaar Bijkeuken Licht",
      "dev":"input",
      "handle_local":
            {
	            "type": "dimmer",
	            "output_dev": "analogoutput",
		    "output_circuit": "2_03",
		    "level": 10
	    },
      "device_normal":"no",
      "state_topic": "homeassistant/bgg/bijkeuken/licht"
   }
```

The HA part can look like:

```
- platform: mqtt
  schema: template
  name: "Woonkamer Nis light"
  unique_id: "woonkamer_nis_licht"
  state_topic: "homeassistant/bgg/woonkamer/nis/licht"
  command_topic: "homeassistant/bgg/woonkamer/nis/licht/set"
  availability_topic: "homeassistant/bgg/woonkamer/nis/licht/available"
  payload_available: "online"
  payload_not_available: "offline"
  command_on_template: >
    {"state": "on"
    , "circuit": "2_04"
    , "dev": "analogoutput"
    {%- if brightness is defined -%}
    , "brightness": {{ brightness }}
    {%- elif brightness is undefined -%}
    , "brightness": 100
    {%- endif -%}
    {%- if transition is defined -%}
    , "transition": {{ transition }}
    {%- endif -%}
    }
  command_off_template: '{"state": "off", "circuit": "2_04", "dev": "analogoutput"}'
  state_template: '{{ value_json.state }}'
  brightness_template: '{{ value_json.brightness }}'
  qos: 0
```

#### 3 - Handle Local Switch (output or relayoutput toggle)
Example unipi_mqtt_config.json of handle local switch (on / off only, relay or digital output used to switch a device or power source to a device).
It will poll the UniPi box and toggle the output to the other state. So on becomes off and visa versa. A MQTT message reflecting this is sent. HA needs to have the topic and payload to recognise a change in the HA GUI.

```{
      "circuit":"UART_4_4_04",
      "description":"TEST IN FUTURE Schakelaar Woonkamer Eker Licht",
      "dev":"input",
      "handle_local":
        {
	        "type": "switch",
	        "output_dev": "output",
		"output_circuit": "2_02"
	},
      "device_normal":"no",
      "state_topic": "homeassistant/bgg/meterkast/testrelay"
   }
```

The HA part of this switch looks like (for me under lights in YAML):
```
- platform: mqtt
  schema: template
  name: "Test Relay 2_02"
  unique_id: "test_relay_2_02"
  state_topic: "homeassistant/bgg/meterkast/testrelay"
  command_topic: "homeassistant/bgg/meterkast/testrelay/set"
  availability_topic: "homeassistant/bgg/meterkast/testrelay/available"
  payload_available: "online"
  payload_not_available: "offline"
  command_on_template: '{"state": "on", "circuit": "2_02", "dev": "output"}'
  command_off_template: '{"state": "off", "circuit": "2_02", "dev": "output"}'
  state_template: '{{ value_json.state }}'
  qos: 0
```

### 1-Wire sensors
You can connect 1-wire sensors to the UniPi (16 cascaded sensors to 1 1-wire port). The sensors allow you to measure things like temperature and humidity. The implementation currently support sensors with model `DS2438` and `DS18B20`. Other might work, but I just don't have them and the script hard-checks for those models. So let me know if you need a change / add here. This info can be found in the UniPi API (exmp. http://192.168.1.125:8080/rest/sensor/28D1EFB708025352 ) The value for "circuit" can be found in the web GUI of the UNiPi.

Config in unipi_mqtt the config file
```
"circuit":"28D1EFB708025352",
"description":"Temperatuur Sensor buiten",
"dev":"temp",
"interval":19,
"state_topic":"unipi/buiten/voordeur/temperatuur"
```

"dev" value options are `"temp"`, `"humidity"` or `"light"`.

Config in HA sensors part:
```
- platform: mqtt
  name: "Buiten Temperatuur"
  unique_id: "buiten_temperatuur"
  state_topic: "unipi/buiten/voordeur/temperatuur"
  unit_of_measurement: "°C"
  value_template: "{{value_json.temperature}}"
  force_update: true
```

NOTE: the `force_update: true` is used to always update the sensor. Home Assistant by default does NOT updates sensor values if they, compared to the latest value, are unchanged. This is optional. Since I run a script to monitor my unipi device based on a regular update (if > 10 min no update = alart) of this value I want it to always update.

## Description of the fields:
 - dev: The input device type on the UniPi
 - circuit: The input circuit on the UniPi
 - description: Description of what you do with this input
 - device_delay: delay to turn device off automatically (used for PIR sensors that work pulse based)
 - device_normal: is device normal open or normal closed
 - unipi_value: what is the current value, used as a "global var"
 - unipi_prev_value: what is the previous value, used as a "global var" to calculate average of multiple values ver time
 - unipi_prev_value_timstamp: when was the last status change. Used for delay based off messages, for example for PIR pulse
 - state_topic: MQTT state topic to send message on
 - handle_local: Use to switch outputs based on a input directly. So no dependency on MQTT broker or HASSIO. Use this for bel and light switches. Does send a MQTT update message to status can change in Home Assistant.
 - interval: value for 1-wire sensors and analog inputs to create an average based on this number of readings and send this avg out.
 

## MQTT messages to change UniPi Outputs
You can send MQTT message to the UniPi box over MQTT to switch outputs. This does not require a config entry on the UniPi since we're sending the device and circuit information in the MQTT message that is handled by the script.

Example for dimmable light (publish from HASS to UniPi to turn on an output)
```
- platform: mqtt
  schema: template
  name: "Voordeur light"
  state_topic: "homeassistant/buiten/voordeur/licht"
  command_topic: "homeassistant/buiten/voordeur/licht/set"
  availability_topic: "homeassistant/buiten/voordeur/licht/available"
  payload_available: "online"
  payload_not_available: "offline"
  command_on_template: >
    {"state": "on"
    , "circuit": "2_02"
    , "dev": "analogoutput"
    {%- if brightness is defined -%}
    , "brightness": {{ brightness }}
    {%- elif brightness is undefined -%}
    , "brightness": 100
    {%- endif -%}
    {%- if effect is defined -%}
    , "effect": "{{ effect }}"
    {%- endif -%}
    {%- if transition is defined -%}
    , "transition": {{ transition }}
    {%- endif -%}
    }
  command_off_template: '{"state": "off", "circuit": "2_02", "dev": "analogoutput"}'
  state_template: '{{ value_json.state }}'
  brightness_template: '{{ value_json.brightness }}'
  qos: 0
```

Switch a relay:
```
- platform: mqtt
  schema: template
  name: "Test Relay 2_02"
  unique_id: "test_relay_2_02"
  state_topic: "unipi1/bgg/meterkast/testrelay"
  command_topic: "unipi1/bgg/meterkast/testrelay/set"
  payload_available: "online"
  payload_not_available: "offline"
  command_on_template: '{"state": "on", "circuit": "2_02", "dev": "output"}'
  command_off_template: '{"state": "off", "circuit": "2_02", "dev": "output"}'
  state_template: "{{ value_json.state }}"
  qos: 0
```

I the device remains offline you need to take out the availability topic line to let HA not check that. 


# Change log

### version 02.2021.1 ("the average" release)
Changes:
 - the 1-wire implementation did not use the average setting. Rewrote the 1-wire part for temp and humidity sensors. You can now add a "interval" variable that counts creates an average. "interval" is the number or readings. Note that 1-wire readings are approx. once every 3 seconds and 0 is the first value so a seeeting of 19 = 20 values. This allows you to greatly reduce the number of updates HA has to handle when the sensors count builds. :-)
 - The same average systems is used for LUX value based on an analog input (I know, rather specific). 

### version 11.2020 (the "Stijn" release)
Changes:
 - New version numbering since that's really cool
 - Made WebSockets listener re-connect on disconnect (like a service interruption of evok) every 5 seconds
 - implemented authentication for MQTT since that is a requirement for HA now
 - adjusted some timeouts to make an external relay work via the bel function
 - Changed the unipi_mqtt.service file to restart a service if the script fails for enhanced resilience

### Version 0.4
Changes:
 - Added authentication for MQTT with username and password variable since the standard MQTT broker in HA requires this from now on.
 - Added a counter function to count pulses coming in on a digital input. Counter totals and counter delta for X time can be sent via MQTT. Personally use this for a water flow meter that procuces pulse for every X ML.
 - Changed the time based interval to a clock instead of incoming messages to be a bit more precise. 
 - Changes handle local for switches. Was sending back a wrong MQTT topic for my HA config to work (MIGHT BE BREAKING CHANGE). 
 - Changed a bug in unipython.py where switch status for on / off was the wrong way around.
 - 0.41 has a small fix to honor the "level" information in unipi_mqtt_config for handle local dimmers.

### Version 0.3
Changes:
 - Changed the thread part so threading and especially the stop thread part now works correctly
 - Changed the MQTT send part to make sure that on a handle local action only 1 message is send (was 4). Now works nicely
 - Revamped the threaded function like duration and transition to be interruptable
 - Changed code for the 1 wire devices. Upgrade of Evok changed the naming convention for those devices from "temp" to "1wire". Now handled again.

### Version 0.2
Changes:
 - Changed handling if DI devices with delay to no longer use previous state for rest of devices, cleaned up json config file. Should fix a bug that crashed the script on certain ON / OFF actions.
 - Implemented a "first run" part to set MQTT messages at script start to reflect actual status of inputs, not last known status maintained in MQTT broker or no status at al. 
 - tested UART (extension module) and that works. Changed config file with example

### Version 0.1
Initial release and documentation in this readme file

## ToDo
  - Something with certificates
  - Use config file for client part too?
  - clean up code more
  - many other yet to discover things.

# Test info

Tested on a UniPi 513 with Extension xS30 running Evok 2.x and Home Assistant 0.102
Used:
 - 0-10v inputs and outputs
 - relay outputs
 - Digital inputs and outputs
 - 1 wire for temp, humidity and light
 - UART Extension module 30

