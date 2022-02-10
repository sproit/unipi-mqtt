#!/usr/bin/python3

# Script to turn on / set level of UNIPI s based on MQTT messages that come in. 
# No fancy coding to see here, please move on (Build by a complete amateur ;-) )
# Matthijs van den Berg / https://github.com/matthijsberg/unipi-mqtt
# MIT License
# version 02.2021.1  (new version numbering since that's really face these days)

# resources used besides google;
# http://jasonbrazeal.com/blog/how-to-build-a-simple-iot-system-with-python/ -
# http://www.diegoacuna.me/how-to-run-a-script-as-a-service-in-raspberry-pi-raspbian-jessie/ TO run this as service -
# https://gist.github.com/beugley/e37dd3e36fd8654553df for stoppable thread part
# Class and functions to create threads that can be stopped,
# so that when a light is still dimming (in a thread since it blocking) but motion is
# detected and lights need to turn on during dimming we kill the thread and start the new action.
# WARNING. If you don't need threading, don't use it. Its not fun ;-).

import os
import gc
import logging
import json
import time
from typing import List

import websocket
from websocket import create_connection
import traceback
from collections import OrderedDict
import statistics
import threading
import math

import paho.mqtt.client as mqtt

from unipipython import unipython

########################################################################################################################
# 											Variables used in the system					 					     ###
########################################################################################################################

from config import *

# Generic Variables
if os.name == "nt":
    logging_path = "./unipi_mqtt.log"
else:
    logging_path = "/var/log/unipi_mqtt.log"
dThreads = {}  # keeping track of all threads running
intervals_average = {}  # dict that we fill with and array per sensors that needs an average value. Number of values in array is based on "interval" var in config file
intervals_counter = {}  # counter to use per device in dict so we know when to stop. :-) global since it iterates and this was the best I could come up with.


#################################################################################
#       Some housekeeping functions to handle threads, logging, etc.          ###
#       NO CHANGES AFTER THIS REQUIRED FOR NORMAL USE!                        ###
#################################################################################

class StoppableThread(threading.Thread):  # Implements a thread that can be stopped.
    def __init__(self, name, target, args=()):
        super(StoppableThread, self).__init__(name=name, target=target, args=args)
        self._status = 'running'

    def stop_me(self):
        if (self._status == 'running'):
            # logging.debug('{}: Changing thread "{}" status to "stopping".'.format(get_function_name(),dThreads[thread_id]))
            self._status = 'stopping'

    def running(self):
        self._status = 'running'

    # logging.debug('{}: Changing thread "{}" status to "running".'.format(get_function_name(),dThreads[thread_id]))
    def stopped(self):
        self._status = 'stopped'

    # logging.debug('{}: Changing thread "{}" status to "stopped".'.format(get_function_name(),dThreads[thread_id]))
    def is_running(self):
        return (self._status == 'running')

    def is_stopping(self):
        return (self._status == 'stopping')

    def is_stopped(self):
        return (self._status == 'stopped')


def StopThread(thread_id):
    # Stops a thread and removes its entry from the global dThreads dictionary.
    logging.warning('{}: STOPthread ID {} .'.format(get_function_name(), thread_id))
    global dThreads
    if thread_id in str(dThreads):
        logging.warning(
            '{}: Thread {} found in thread list: {} , checking if running or started...'.format(get_function_name(),
                                                                                                dThreads[thread_id],
                                                                                                dThreads))
        thread = dThreads[thread_id]
        if (thread.is_stopping()):
            logging.warning(
                '{}: Thread {} IS found STOPPING in running threads: {} , waiting till stop complete for thread {}.'.format(
                    get_function_name(), thread_id, dThreads, dThreads[thread_id]))
        if (thread.is_running()):
            logging.warning('{}: Thread {} IS found active in running threads: {} , proceeding to stop {}.'.format(
                get_function_name(), thread_id, dThreads, dThreads[thread_id]))
            logging.warning('{}: Stopping thread "{}"'.format(get_function_name(), thread_id))
            thread.stop_me()
            logging.warning(
                '{}: thread.stop_me finished. Now running threads and status: {} .'.format(get_function_name(),
                                                                                           dThreads))
            thread.join(
                10)  # implemented a timeout of 10 since the join here is blocking and halts the complete script. this allows the main function to continue, but is an ERROR since a thread is not joining. Most likely an function that hangs (function needs to end before a join is succesfull)!
            thread.stopped()
            logging.warning('{}: Stopped thread "{}"'.format(get_function_name(), thread_id))
            del dThreads[thread_id]
            logging.warning('{}: Remaining running threads are "{}".'.format(get_function_name(), dThreads))
        else:
            logging.warning('{}: Thread {} not running or started.'.format(get_function_name(), dThreads[thread_id]))
    else:
        logging.warning(
            '{}: Thread {} not found in global thread var: {}.'.format(get_function_name(), thread_id, dThreads))


def get_function_name():
    return traceback.extract_stack(None, 2)[0][2]


def every(delay, task):
    next_time = time.time() + delay
    while True:
        time.sleep(max(0, next_time - time.time()))
        try:
            task()
        except Exception:
            logging.warning('{}: Problem while executing repetitive task.'.format(get_function_name()))
        # skip tasks if we are behind schedule:
        next_time += (time.time() - next_time) // delay * delay + delay


########################################################################################################################
###   Functions to handle the incomming MQTT messages, filter, sort, and kick off the action functions to switch.    ###
########################################################################################################################

def on_mqtt_message(mqttc, userdata, msg):
    # print(msg.topic+" "+str(msg.payload))
    if "set" in msg.topic:
        mqtt_msg = str(msg.payload.decode("utf-8", "ignore"))
        logging.debug('{}: Message "{}" on input.'.format(get_function_name(), mqtt_msg))
        mqtt_msg_history = mqtt_msg
        print(mqtt_msg)
        if mqtt_msg.startswith("{"):
            try:
                mqtt_msg_json = json.loads(mqtt_msg,
                                           object_pairs_hook=OrderedDict)  # need the orderedDict here otherwise the order of the MQTT message is changed, that will bnreak the return message and than the device won't turn on in HASSIO
            except ValueError as e:
                logging.error('{}: Message "{}" not a valid JSON - message not processed, error is "{}".'.format(
                    get_function_name(), mqtt_msg, e))
            else:
                logging.debug(
                    '{}: Message "{}" is a valid JSON, processing json in handle_json.'.format(get_function_name(),
                                                                                               mqtt_msg_json))
                handle_json(msg.topic, mqtt_msg_json)
        else:
            logging.debug(
                "{}: Message \"{}\" not JSON format, processing other format.".format(get_function_name(), mqtt_msg))
            handle_other(msg.topic, mqtt_msg)


# Main function to handle incoming MQTT messages, check content en start the correct function to handle the request.
# All time-consuming, and thus blocking actions, are threaded.
def handle_json(ms_topic, message: dict):
    global dThreads
    try:
        # We NEED a dev in the message as this targets a circuit type (analog / digital inputs, etc.) on the UniPi
        dev_value = message['dev']
        # We also NEED a circuit in the message to be able to target a circuit on the UniPi
        circuit_value = message['circuit']
        # state, what do we need to do
        state_value = message['state']
        # Transition, optional. You can fade analog outputs slowly.
        # Transition is the amount of seconds you want to fade to take
        # (seconds always applied to 0-100%, so 0-25% = 25% of seconds)
        transition_value = message.get('transition', None)
        # Brightness, if you switch lights with 0-10 volt we translate the input value (0-255) to 0-10 and consider this brightness
        brightness_value = message.get('brightness', None)
        # Repeat, if present this will trigger an on - off action x amount of times.
        # I use this to trigger a relay multiple times to let a bel ring x amount of times.
        repeat_value = message.get('repeat', None)
        # Duration is used to switch a output on for x seconds. IN my case used to open electrical windows.
        duration_value = message.get('duration', None)
        # Effect, not actively used yet, for future reference.
        effect_value = message.get('effect', None)
        logging.debug('Device: {}, Circuit: {}, State: {} .'.format(dev_value, circuit_value, state_value))
    except:
        logging.error(
            '{}: Unhandled exception. Looks like input is not valid dict / json. Message data is: "{}".'.format(
                get_function_name(), message))
    # id = circuit_value
    thread_id = dev_value + circuit_value
    logging.debug(
        '{}: Valid WebSocket input received, processing message "{}"'.format(get_function_name(), message))
    if transition_value is not None:
        if brightness_value is not None:
            logging.warning(
                '{}: starting "transition" message handling for dev "{}" circuit "{}" to value "{}" in {} s. time.'.format(
                    get_function_name(), circuit_value, state_value, brightness_value, transition_value))
            if brightness_value > 255:
                logging.error(
                    '{}: Brightness input is greater than 255, 255 is max value! Setting Brightness to 255.'.format(
                        get_function_name()));
                brightness_value = 255
            StopThread(thread_id)
            dThreads[thread_id] = StoppableThread(name=thread_id, target=transition_brightness, args=(
                brightness_value, transition_value, dev_value, circuit_value, ms_topic, message))
            dThreads[thread_id].start()
            logging.warning('TEMP threads {}:'.format(dThreads))
            logging.warning(
                '{}: started thread "{}" for "transition" of dev "{}" circuit "{}".'.format(get_function_name(),
                                                                                            dThreads[thread_id],
                                                                                            circuit_value,
                                                                                            state_value))
        else:
            logging.error(
                '{}: Processing "transition", but missing argument "brightness", aborting. Message data is "{}".'.format(
                    get_function_name(), message))
    elif brightness_value is not None:
        logging.debug(
            '{}: starting "brightness" message handling for dev "{}" circuit "{}" to value "{}" (not in thread).'.format(
                get_function_name(), circuit_value, state_value, brightness_value))
        if (brightness_value > 255):    logging.error(
            '{}: Brightness input is greater than 255, 255 is max value! Setting Brightness to 255.'.format(
                get_function_name())); brightness_value = 255
        StopThread(thread_id)
        set_brightness(brightness_value, circuit_value, ms_topic, message)  # not in thread as this is not blocking
    elif effect_value is not None:
        logging.error('{}: Processing "effect", but not yet implemented, aborting. Message data is "{}"'.format(
            get_function_name(), message))
    elif duration_value is not None:
        logging.debug(
            '{}: starting "duration" message handling for dev "{}" circuit "{}" to value "{}" for {} sec.'.format(
                get_function_name(), circuit_value, state_value, state_value, duration_value))
        StopThread(thread_id)
        dThreads[thread_id] = StoppableThread(name=thread_id, target=set_duration, args=(
            dev_value, circuit_value, state_value, duration_value, ms_topic, message))
        dThreads[thread_id].start()
        logging.debug('{}: started thread "{}" for "duration" of dev "{}" circuit "{}".'.format(get_function_name(),
                                                                                                dThreads[thread_id],
                                                                                                circuit_value,
                                                                                                state_value))
    elif repeat_value is not None:
        logging.debug('{}: starting "repeat" message handling for dev "{}" circuit "{}" for {} time'.format(
            get_function_name(), circuit_value, state_value, int(repeat_value)))
        StopThread(thread_id)
        dThreads[thread_id] = StoppableThread(name=thread_id, target=set_repeat,
                                              args=(dev_value, circuit_value, int(repeat_value), ms_topic, message))
        dThreads[thread_id].start()
        logging.debug('{}: started thread "{}" for "repeat" of dev "{}" circuit "{}".'.format(get_function_name(),
                                                                                              dThreads[thread_id],
                                                                                              circuit_value,
                                                                                              state_value))
    elif state_value == "on" or state_value == "off":
        logging.debug(
            '{}: starting "state value" message handling for dev "{}" circuit "{}" to value "{}" (not in thread).'.format(
                get_function_name(), circuit_value, state_value, state_value))
        StopThread(thread_id)
        set_state(dev_value, circuit_value, state_value, ms_topic, message)  # not in thread, not blocking
    else:
        logging.error('{}: No valid actionable item found!')


def handle_other(ms_topic,
                 message):  # TODO, initialy started to handle ON and OFF messages, but since we require dev and circuit this doesn't work. Maybe for future ref. and use config file?
    logging.warning(
        '"{}": function not yet implemented! Received message "{}" here.'.format(get_function_name(), message))


########################################################################################################################
#       Functions to handle WebSockets (UniPi) inputs to filter, sort, and kick off the actions via MQTT Publish.      #
########################################################################################################################

def ws_sanity_check(message):
    # Function to handle all messaging from Websocket Connection and do input validation
    # MEMO TO SELF - print("{}. {} appears {} times.".format(i, key, wordBank[key]))
    tijd = time.time()
    # Check if message is list or dict (Unipi sends most as list in dics, but modbus sensors as dict
    mesdata = json.loads(message)
    if type(mesdata) is dict:
        message_sort(mesdata)
        logging.debug('DICT message without converting (will be processed): {}'.format(message))
    else:
        for message_dev in mesdata:  # Check if there are updates over websocket and run functions to see if we need to update anything
            if type(message_dev) is dict:
                message_sort(message_dev)
            else:
                logging.debug('Ignoring received data, it is not a dict: {}'.format(message_dev))


# Check if we need to switch off something. This is handled here since this function triggers every second (analoge input update freq.).
# off_commands()
# fire a function that checks things based on time (off_commands does that too, but to switch devices off)
# timed_updates() #for now integrated in off_commands since it uses the same logic.

def message_sort(message_dev):
    # Function to sort to different websocket messages for processing based on device type (dev)
    if message_dev['dev'] == "input":
        dev_di(message_dev)
    elif message_dev['dev'] == "ai":
        dev_ai(message_dev)
    elif message_dev[
        'dev'] == "temp":  # temp is being used for the modbus temp only sensors, multi sensors in modbus use dev: 1wdevice since latest evok version
        dev_modbus(message_dev)
    elif message_dev[
        'dev'] == "1wdevice":  # modules I tested so far as indicator (U1WTVS, U1WTD) that also report humidity and light intensity.
        dev_modbus(message_dev)
    elif message_dev['dev'] == "relay":  # not sure what this does yet, not worked with it much.
        dev_relay(message_dev)
    elif message_dev['dev'] == "wd":  # Watchdog notices, ignoring and only show in debug logging level (std off)
        logging.debug('{}: UNIPI WatchDog Notice: {}'.format(get_function_name(), message_dev))
    elif message_dev['dev'] == "ao":
        logging.debug(
            '{}: Received and AO message in web-socket input, most likely a result from a switch action that also triggers this. ignoring'.format(
                get_function_name(), message_dev))
    else:
        logging.warning(
            '{}: Message has no "dev" type of "input", "ai", "relay" or string "DS". Received input is : {} .'.format(
                get_function_name(), message_dev))


def get_configured_device(circuit: int, device_type: str):
    for configured_device in devdes:
        if configured_device['circuit'] == circuit and configured_device['dev'] == device_type:
            return configured_device
    return None


def dev_di(message_dev):
    # Function to handle Digital Inputs from WebSocket (UniPi)
    logging.debug('{}: SOF'.format(get_function_name()))
    tijd = time.time()
    in_list_cntr = 0
    for config_dev in devdes:
        if config_dev['circuit'] == message_dev['circuit'] and config_dev['dev'] == 'input':
            # To check if device switch is in config file and is an input
            raw_mode_presence = 'raw_mode' in config_dev  # becomes True is "raw_mode" is found in config
            device_type_presence = 'device_type' in config_dev  # becomes True is "device_type" is found in config
            handle_local_presence = 'handle_local' in config_dev  # becomes True is "handle local" is found in config
            device_delay_presence = 'device_delay' in config_dev  # becomes True is "device_delay" is found in config
            if device_delay_presence:
                if config_dev['device_delay'] == 0: device_delay_presence = False
            unipi_value_presence = 'unipi_value' in config_dev
            # If raw mode is selected a WEbdav message will only be transformed into a MQTT message, nothing else.
            if raw_mode_presence:  # RAW modes just pushes all fields in the websocket message out via MQTT TODO
                logging.error('    {}: TO BE IMPLEMENTED".'.format(
                    get_function_name()))  # todo. Can we just add input iD and raw to make this work? stop the loop here?
                device_type_presence = 'device_type' in config_dev  # becomes True is "device_type" is found in config
            # Implemented device types per 2020 to filter counter for pulse based counter devices like water meters.
            # Just count counter on NO devices that turn on.
            # Since WebDav does not send a trigger for every update we calculate the delta betwee this and the previous update.
            elif device_type_presence:
                if config_dev['device_type'] == 'counter':
                    if (('max_delay_value' in config_dev) or ('device_delay' in config_dev)) is False:
                        logging.error('{}: Error in Config file, missing fields'.format(get_function_name()))
                    else:
                        config_dev['counter_value'] = message_dev['counter']
                        if config_dev["counter_value"] == 0:  # for bootup of script to set initial value
                            config_dev["counter_value"] = message_dev["counter"]
                            if config_dev["unipi_value"] == 0:
                                config_dev["unipi_value"] = (message_dev["counter"] - 1)
                else:
                    logging.error('{}: Unknown device type "{}", breaking.'.format(get_function_name(),
                                                                                   config_dev['device_type']))
            elif device_delay_presence:
                # Running devices with delay to reswitch
                # (like pulse bsed motion sensors that pulse on presence ever 10 sec to on)
                # Using no / nc and delay to switch
                # We should only see "ON" here! Off messages are handled in function off_commands
                logging.debug('{}: Loop with delay with message: {}'.format(get_function_name(), message_dev))
                if tijd >= (config_dev['unipi_prev_value_timstamp'] + config_dev['device_delay']):
                    if message_dev['value'] == 1:
                        if config_dev['unipi_value'] == 1:
                            logging.debug('{}: received status 1 is actual status: {}'.format(get_function_name(),
                                                                                              message_dev))  # nothing to do, since there is not status change. First in condition to easy load ;-)
                        elif config_dev['device_normal'] == 'no':
                            publish_state(config_dev['state_topic'], payload_on)
                            # check if device is normal status is OPEN or CLOSED loop to turn ON / OFF
                            if handle_local_presence:
                                handle_local_switch_on_or_toggle(message_dev, config_dev)
                            config_dev['unipi_value'] = message_dev['value']
                            config_dev['unipi_prev_value_timstamp'] = tijd
                        elif config_dev['device_normal'] == 'nc':  # should never run!
                            # should not do anything since and off commands are handled in off_commands def.
                            logging.debug(
                                '{}: This should do nothing since off commands are not handled here. Config: {}, Received message: {}'.format(
                                    get_function_name(), message_dev, config_dev))
                        else:
                            logging.error(
                                '{}: Unhandled Exception 1, config: {}, status: {}, normal_config: {}, {}, {}'.format(
                                    get_function_name(), config_dev['unipi_value'], message_dev['value'],
                                    config_dev['device_normal'], message_dev['circuit'], config_dev['state_topic']))
                    elif message_dev['value'] == 0:
                        if config_dev['unipi_value'] == 0:
                            logging.debug('{}: received status 0 is actual status: {}'.format(get_function_name(),
                                                                                              message_dev))  # nothing to do, since there is not status change. First in condition to easy load ;-)
                        elif config_dev['device_normal'] == 'no':  # should never run!
                            # should not do anything since and off commands are handled in off_commands def.
                            logging.debug(
                                '{}: This should do nothing since off commands are not handled here. Config: {}, Received message: {}'.format(
                                    get_function_name(), message_dev, config_dev))
                        elif config_dev['device_normal'] == 'nc':
                            publish_state(config_dev['state_topic'], payload_on)
                            if handle_local_presence: handle_local_switch_on_or_toggle(message_dev, config_dev)
                            config_dev['unipi_value'] = message_dev['value']
                            config_dev['unipi_prev_value_timstamp'] = tijd
                        else:
                            logging.error(
                                '{}: Unhandled Exception 2, config: {}, status: {}, normal_config: {}, {}, {}'.format(
                                    get_function_name(), config_dev['unipi_value'], message_dev['value'],
                                    config_dev['device_normal'], message_dev['circuit'], config_dev['state_topic']))
                    else:
                        logging.error(
                            '{}: Device value not 0 or 1 as expected for Digital Input. Message is: {}'.format(
                                get_function_name(), message_dev))
                else:
                    config_dev['unipi_prev_value_timstamp'] = tijd
            else:
                # Running devices without delay, always switching on / of based on UniPi Digital Input
                logging.debug('{}: Loop without delay with message: {}'.format(get_function_name(), message_dev))
                if message_dev['value'] == 1:
                    if config_dev['device_normal'] == 'no':
                        if device_type_presence:
                            if config_dev['device_type'] == 'counter':
                                mqtt_set_counter(message_dev, config_dev)
                            else:
                                logging.error('{}: Unknown device type "{}", breaking.'.format(get_function_name(),
                                                                                               config_dev[
                                                                                                   'device_type']))  # check if device is normal status is OPEN or CLOSED loop to turn ON / OFF
                        elif handle_local_presence:
                            handle_local_switch_on_or_toggle(message_dev, config_dev)
                        else:
                            publish_state(config_dev['state_topic'], payload_on)
                            # sends MQTT command, removed as test since this is done in handle_local_switch_toggle too
                    elif config_dev['device_normal'] == 'nc':
                        # Turn off devices that switch to their normal mode and have no delay configured! Delayed devices will be turned off somewhere else
                        if handle_local_presence:
                            pass  # OLD: handle_local_switch_toggle(message_dev,config_dev) # we do a pass since a pulse based switch sends a ON and OFF in 1 action, we only need 1 action to happen!
                        else:
                            publish_state(config_dev['state_topic'], payload_off)
                            # sends MQTT command, removed as test since this is done in handle_local_switch_toggle too
                    else:
                        logging.debug('{}: ERROR 1, config: {}, normal_config: {}, {}, {}'.format(get_function_name(),
                                                                                                  message_dev['value'],
                                                                                                  config_dev[
                                                                                                      'device_normal'],
                                                                                                  message_dev[
                                                                                                      'circuit'],
                                                                                                  config_dev[
                                                                                                      'state_topic']))
                elif message_dev['value'] == 0:
                    if config_dev['device_normal'] == 'no':
                        if handle_local_presence:
                            pass  # - OLD:handle_local_switch_toggle(message_dev,config_dev)
                        else:
                            publish_state(config_dev['state_topic'], payload_off)
                            # Turn off devices that switch to their normal mode and have no delay configured!
                            # Delayed devices will be turned off somewhere else
                    elif config_dev['device_normal'] == 'nc':
                        if device_type_presence:
                            if config_dev['device_type'] == 'counter':
                                mqtt_set_counter(message_dev, config_dev)
                            else:
                                logging.error('{}: Unknown device type "{}", breaking.'.format(get_function_name(),
                                                                                               config_dev[
                                                                                                   'device_type']))
                        elif handle_local_presence:
                            handle_local_switch_on_or_toggle(message_dev, config_dev)
                        else:
                            publish_state(config_dev['state_topic'], payload_on)
                    else:
                        logging.debug('{}: ERROR 2, config: {}, normal_config: {}, {}, {}'.format(get_function_name(),
                                                                                                  message_dev['value'],
                                                                                                  config_dev[
                                                                                                      'device_normal'],
                                                                                                  message_dev[
                                                                                                      'circuit'],
                                                                                                  config_dev[
                                                                                                      'state_topic']))
                else:
                    logging.error('{}: Device value not 0 or 1 as expected for Digital Input. Message is: {}'.format(
                        get_function_name(), message_dev))


def dev_ai(message_dev):
    # Function to handle Analoge Inputs from WebSocket (UniPi), mainly focussed on LUX from analoge input now. using a sample rate to reduce rest calls to domotics
    for config_dev in devdes:
        if config_dev['circuit'] == message_dev['circuit'] and config_dev['dev'] == "ai":
            int_presence = 'interval' in config_dev
            # check to see if "interval" in config. If not throw an error. If you want to disable average, set to 0.
            if int_presence:
                cntr = intervals_counter[config_dev['dev'] + config_dev['circuit']]
                if cntr <= config_dev['interval']:
                    intervals_average[config_dev['dev'] + config_dev['circuit']][cntr] = float(
                        round(message_dev['value'], 3))
                    intervals_counter[config_dev['dev'] + config_dev['circuit']] += 1
                else:
                    # write LUX to MQTT here.
                    lux = int(
                        round((statistics.mean(intervals_average[config_dev['dev'] + config_dev['circuit']]) * 200), 0))
                    mqtt_set_lux(config_dev['state_topic'], lux)
                    config_dev['unipi_avg_cntr'] = 0
                    logging.debug('PING Received WebSocket data and collected 30 samples of lux data : {}'.format(
                        message_dev))  # we're loosing websocket connection, debug
                    intervals_counter[config_dev['dev'] + config_dev['circuit']] = 0
            else:
                logging.error(
                    '{}: CONFIG ERROR : 1-WIRE sensor "{}" is missing "interval" in config file. Set to 0 to disable or set sampling rate with a higher value.'.format(
                        get_function_name(), message_dev))


def dev_relay(message_dev):
    print(message_dev)
    device = get_configured_device(message_dev['circuit'], message_dev['dev'])
    if device is None:
        return
    topic = device['state_topic']
    publish_state(topic, payload_off if message_dev['value'] == 0 else payload_on)


def dev_modbus(message_dev):
    # Function to handle Analoge Inputs from WebSocket (UniPi), mainly focussed on LUX from analoge input now. using a sample rate to reduce MQTT massages. TODO needs to be improved!
    for config_dev in devdes:
        try:
            if (config_dev['circuit'] == message_dev['circuit'] and (
                    config_dev['dev'] == "temp" or config_dev['dev'] == "humidity" or config_dev['dev'] == "light")):
                int_presence = 'interval' in config_dev  # check to see if "interval" in config. If not throw an error. If you want to disable average, set to 0.
                if int_presence:
                    cntr = intervals_counter[config_dev['dev'] + config_dev['circuit']]
                    # config for 1-wire temperature sensors intervals_average[config_dev['dev']+config_dev['circuit']]
                    if config_dev['dev'] == "temp":
                        if cntr <= config_dev['interval']:
                            if message_dev['typ'] == "DS18B20":
                                if -55 <= float(
                                        message_dev['value']) <= 125:  # sensor should be able to do -55 to +125 celcius
                                    intervals_average[config_dev['dev'] + config_dev['circuit']][cntr] = float(
                                        message_dev['value'])
                                    intervals_counter[config_dev['dev'] + config_dev['circuit']] += 1
                                else:
                                    logging.error(
                                        '{}: Message "{}" is out of range, temp smaller than -55 or larger than 125.'.format(
                                            get_function_name(), message_dev))
                            elif message_dev['typ'] == "DS2438":
                                if -55 <= float(
                                        message_dev['temp']) <= 125:  # sensor should be able to do -55 to +125 celcius
                                    intervals_average[config_dev['dev'] + config_dev['circuit']][cntr] = float(
                                        message_dev['temp'])
                                    intervals_counter[config_dev['dev'] + config_dev['circuit']] += 1
                                else:
                                    logging.error(
                                        '{}: Message "{}" is out of range, temp smaller than -55 or larger than 125.'.format(
                                            get_function_name(), message_dev))
                            else:
                                logging.error('{}: Unknown Device sensor type {} in config'.format(get_function_name(),
                                                                                                   message_dev['typ']))
                        else:
                            avg_temperature = statistics.mean(
                                intervals_average[config_dev['dev'] + config_dev['circuit']])
                            avg_temperature = round(avg_temperature, 1)
                            mqtt_set_temp(config_dev['state_topic'], avg_temperature)
                            intervals_counter[config_dev['dev'] + config_dev['circuit']] = 0
                    # config for 1-wire humidity sensors
                    elif config_dev['dev'] == "humidity":
                        if cntr <= config_dev['interval']:
                            if message_dev['typ'] == "DS2438":
                                if 0 <= float(message_dev['humidity']) <= 100:
                                    intervals_average[config_dev['dev'] + config_dev['circuit']][cntr] = float(
                                        round(message_dev['humidity'], 1))
                                    intervals_counter[config_dev['dev'] + config_dev['circuit']] += 1
                                else:
                                    logging.error(
                                        '{}: Message "{}" is out of range, humidity smaller or larger than 100.'.format(
                                            get_function_name(), message_dev))
                            else:
                                logging.error('{}: Unknown Device sensor type {} in config'.format(get_function_name(),
                                                                                                   message_dev['typ']))
                        else:
                            avg_humidity = float(
                                statistics.mean(intervals_average[config_dev['dev'] + config_dev['circuit']]))
                            avg_humidity = round(avg_humidity, 1)
                            mqtt_set_humi(config_dev['state_topic'], avg_humidity)
                            intervals_counter[config_dev['dev'] + config_dev['circuit']] = 0
                    # config for 1-wire light / lux sensors
                    elif config_dev['dev'] == "light":
                        if cntr <= config_dev['interval']:
                            if message_dev['typ'] == "DS2438":
                                if 0 <= float(message_dev['vis']) <= 0.25:
                                    intervals_average[config_dev['dev'] + config_dev['circuit']][cntr] = float(
                                        round(message_dev['vis'], 1))
                                    intervals_counter[config_dev['dev'] + config_dev['circuit']] += 1
                                else:
                                    logging.error(
                                        '{}: Message "{}" is out of range, humidity smaller or larger than 100.'.format(
                                            get_function_name(), message_dev))
                            else:
                                logging.error('{}: Unknown Device sensor type {} in config'.format(get_function_name(),
                                                                                                   message_dev['typ']))
                        else:
                            avg_illumination = float(
                                statistics.mean(intervals_average[config_dev['dev'] + config_dev['circuit']]))
                            if avg_illumination < 0:
                                avg_illumination = 0  # sometimes I see negative values that would make no sense, make that a 0
                            # try to match this with LUX from other sensors, 0 to 2000 LUX so need to calculate from 0 to 0.25 volt to match that. TODO is 2000 LUX = 0.25 or more?
                            avg_illumination = avg_illumination * 8000
                            avg_illumination = round(avg_illumination, 0)
                            mqtt_set_lux(config_dev['state_topic'], avg_illumination)
                            intervals_counter[config_dev['dev'] + config_dev['circuit']] = 0
                else:
                    logging.error(
                        '{}: CONFIG ERROR : 1-WIRE sensor "{}" is missing "interval" in config file. Set to 0 to disable or set sampling rate with a higher value.'.format(
                            get_function_name(), message_dev))
        except ValueError as e:
            logging.error(
                'Message "{}" not a valid JSON - message not processed, error is "{}".'.format(message_dev, e))


### Functions to switch outputs on the UniPi
### Used for incomming messages from MQTT and switches UniPi outputs conform the message received

def set_repeat(dev, circuit, repeat, topic, message):
    logging.debug('   {}: SOF with message "{}".'.format(get_function_name(), message))
    global dThreads
    thread_id = dev + circuit
    thread = dThreads[thread_id]
    ctr = 0
    while repeat > ctr and thread.is_running():
        stat_code_on = (unipy.set_on(dev, circuit))
        time.sleep(0.1)  # time for output on
        stat_code_off = (unipy.set_off(dev, circuit))
        if ctr == 0:  # set MQTT responce on so icon turn ON while loop runs
            mqtt_ack(topic, message)
        ctr += 1
        time.sleep(0.25)  # sleep between output, maybe put this in var one day.
    else:
        if thread.is_stopping():
            logging.warning(
                '   {}: Thread {} was given stop signal and stop before finish. Leaving the cleaning of thread information to "def StopThread". NOT sending final MQTT messages'.format(
                    get_function_name(), thread_id))
            unipy.set_off(dev, circuit)  # extra off since we need to make sure my bel is off, or it will burn out. :-(
        else:
            if (int(stat_code_off) == 200 or int(stat_code_on) == 200):
                # Need to disable switch in HASS with message like {"circuit": "2_01", "dev": "relay", "state": "off"} where org message is {"circuit": "2_01", "dev": "relay", "repeat": "2", "state": "pulse"}.
                message.pop("repeat")  # remove repeat from final mqtt ack with ordered dict action
                message.update({"state": "off"})  # replace state "pulse" with "off" with ordered dict action
                mqtt_ack(topic, message)
                logging.info(
                    '    {}: Successful ran function on dev {} circuit {} for {} times.'.format(get_function_name(),
                                                                                                dev, circuit, repeat))
            else:
                logging.error(
                    '   {}: Error setting device {} circuit {} on UniPi, got error "{}" back when posting via rest.'.format(
                        get_function_name(), dev, circuit, stat_code_off))
            logging.info(
                '   {}: Successful finished thread {}, now deleting thread information from global thread var'.format(
                    get_function_name(), thread_id))
            del dThreads[thread_id]
    logging.debug('   {}: EOF.'.format(get_function_name()))


# SET A DEVICE STATE, NOTE: json keys are put in order somewhere, and for the ack message to hassio to work it needs to be in the same order (for switches as template is not available, only on / off)
def set_state(dev, circuit, state, topic, message):
    logging.debug('   {}: SOF with message "{}".'.format(get_function_name(), message))
    if (dev == "analogoutput" and state == "on"):
        logging.error(
            '   {}: We can not switch an analog output on since we don not maintain last value, not sure to witch value to set output. Send brightness along to fix this'.format(
                get_function_name()))
    elif (dev == "relay" or dev == "output" or (dev == "analogoutput" and state == "off")):
        if state == 'on':
            stat_code = (unipy.set_on(dev, circuit))
        elif state == 'off':
            stat_code = (unipy.set_off(dev, circuit))
        else:
            stat_code = '999'
        if int(stat_code) == 200:
            mqtt_ack(topic, message)
            logging.info(
                '    {}: Successful ran function on device {} circuit {} to state {}.'.format(get_function_name(), dev,
                                                                                              circuit, state))
        else:
            logging.error(
                '   {}: Error setting device {} circuit {} on UniPi, got error "{}" back when posting via rest.'.format(
                    get_function_name(), dev, circuit, stat_code.status_code))
    else:
        logging.error('   {}: Unhandled exception in function.'.format(get_function_name()))
    del dThreads[thread_id]
    logging.debug('   {}: EOF.'.format(get_function_name()))


def set_duration(dev, circuit, state, duration, topic,
                 message):  # Set to switch on for a certain amount of time, I use this to open a rooftop window so for example 30 = 30 seconds
    logging.debug('   {}: SOF with message "{}".'.format(get_function_name(), message))
    global dThreads
    thread_id = dev + circuit
    thread = dThreads[thread_id]
    counter = int(duration)
    if (dev == "analogoutput" and state == "on"):
        logging.error(
            '   {}: We can not switch an analog output on since we don not maintain last value, not sure to witch value to set output. Send brightness along to fix this'.format(
                get_function_name()))
    elif (dev == "relay" or dev == "output" or (dev == "analogoutput" and state == "off")):
        logging.info(
            '   {}: Setting {} device {} to state {} for {} seconds.'.format(get_function_name(), dev, circuit, state,
                                                                             time))
        if state == 'on':
            rev_state = "off"
            stat_code = (unipy.set_on(dev, circuit))
        elif state == 'off':
            rev_state = "on"
            stat_code = (unipy.set_off(dev, circuit))
        if int(stat_code) == 200:  # sending return message straight away otherwise the swithc will only turn on after delay time
            mqtt_ack(topic, message)
            logging.info('    {}: Set {} for circuit "{}".'.format(get_function_name(), state, circuit))
        else:
            logging.error(
                '   {}: error switching device {} on UniPi {}.'.format(get_function_name(), circuit, stat_code))
        while counter > 0 and thread.is_running():
            time.sleep(1)
            counter -= 1
        else:  # handled when thread finishes by completion or external stop signal (StopThread function) #time.sleep(int(duration)) #old depriciated for stoppable thread
            if state == 'on':
                stat_code = (unipy.set_off(dev, circuit))
                message.update({"state": "off"})  # need to change on to off in mqtt message
            elif state == 'off':
                stat_code = (unipy.set_on(dev, circuit))
                message.update({"state": "on"})  # need to change on to off in mqtt message
            if int(stat_code) == 200:  # sending return message straight away otherwise the swithc will only turn on after delay time
                mqtt_ack(topic, message)
                logging.info('    {}: Set {} for circuit "{}".'.format(get_function_name(), rev_state, circuit))
            else:
                logging.error('   {}: error switching device {} to {} on UniPi {}.'.format(get_function_name(), circuit,
                                                                                           rev_state, stat_code))
            if thread.is_stopping():
                logging.warning(
                    '   {}: Thread {} was given stop signal and stop before finish. Leaving the cleaning of thread information to "def StopThread". NOT sending final MQTT messages'.format(
                        get_function_name(), thread_id))
            else:
                logging.info(
                    '   {}: Successful Finished thread {}, now deleting thread information from global thread var'.format(
                        get_function_name(), thread_id))
                del dThreads[thread_id]
    logging.debug('    {}: EOF.'.format(get_function_name()))


def set_brightness(desired_brightness, circuit, topic, message):
    logging.debug('   {}: Starting with message "{}".'.format(get_function_name(), message))
    brightness_volt = round(int(desired_brightness) / 25.5, 2)
    stat_code = (unipy.set_level(circuit, brightness_volt))
    if stat_code == 200:
        mqtt_ack(topic, message)
        logging.info('    {}: Set {} for circuit "{}".'.format(get_function_name(), state, circuit))
    else:
        logging.error("Error switching on device on UniPi: %s ", stat_code.status_code)
    logging.debug('   {}: EOF.'.format(get_function_name()))


def transition_brightness(desired_brightness, trans_time, dev, circuit, topic, message):
    logging.debug('   {}: Starting function with message "{}".'.format(get_function_name(), message))
    global dThreads
    thread_id = dev + circuit
    thread = dThreads[thread_id]
    logging.info('   {}:thread information from global thread var {}'.format(get_function_name(), dThreads))
    trans_step = round(float(trans_time) / 100,
                       3)  # determine time per step for 100 steps. Fix for 100 so dimming is always the same speed, independent of from and to levels
    current_level = unipy.get_circuit(dev, circuit)  # get current circuit level from unipi REST
    desired_level = round(float(desired_brightness) / 25.5,
                          1)  # calc desired level to 1/100 in stead of 256 steps for 0-10 volts
    print(current_level['value'])
    delta_level = (desired_level - current_level['value'])  # determine delta based on from and to levels
    number_steps = abs(round(delta_level * 10, 0))  # determine number of steps based on from and to level
    new_level = current_level['value']
    execution_error = 2  # start with debugging to based return message on
    id = circuit
    logging.debug(
        '   {}: Running with Current Level: {} and Desired Level: {} resulting in a delta of {} and {} number of steps to get there'.format(
            get_function_name(), current_level['value'], desired_level, delta_level, number_steps))
    if (number_steps != 0):
        if (delta_level != number_steps):
            # we need to set a start level via MQTT here as otherwise the device won't show as on when stating transition. Do not include in loop, too slow.
            step_increase = float(delta_level / number_steps)
            # logging.debug('TRANSITION DEBUG 2; number of steps: {} and tread.is
            # setting up a websocket connect here to send the change commands over. Cannot go to global WS since that is in a function and that won't accept commands from here. Maybe one day change to asyncio websocket?_running: {}'.format(number_steps,thread_status))
            logging.info('creating conn')
            print('creating conn')
            short_lived_ws = create_connection("ws://" + ws_server + ":1080/ws")
            logging.info('created conn')
            ### Using the stop_thread function to interrupt when needed. Thread.is_running makes sure we listen to external stop signals ###
            while int(number_steps) > 0 and thread.is_running():
                new_level = round(new_level + step_increase, 1)
                stat_code = 1  # (unipy.set_level(circuit, new_level))
                short_lived_ws.send(
                    '{"cmd":"set","dev":"' + dev + '","circuit":"' + circuit + '","value":' + str(new_level) + '}')
                # Test, send mqtt message to switch device on on every change (maybe throttle in future/). If we don't HA will still thinks it's off while the loop turned it on. With long times this can mess up automations
                temp_level = math.ceil(new_level * 25.5)
                message.update(
                    {"brightness": temp_level})  # replace requested level with actual level in orderd dict action
                mqtt_ack(topic, message)
                number_steps -= 1
                if number_steps > 0:
                    time.sleep(trans_step)
                elif number_steps == 0:
                    logging.info('   {}: Done setting brightness via WebSocket.'.format(get_function_name()))
                    # NEXT CODE IS TO CHECK IS COMMAND WAS SUCCESFULL
                    time.sleep(
                        1.5)  # need a sleep here since getting actual value back is slow sometimes, it takes about a second to get the final value.
                    actual_level = unipy.get_circuit(dev, circuit)
                    logging.info('   {}: Got actual level of "{}" back from function unipy.get_circuit.'.format(
                        get_function_name(), actual_level))
                    if (round(actual_level['value'], 1) != desired_level):
                        execution_error == 1  # TOT Need to changed this to 0 so i always send back actual status of lamp via MQTT (had issue that mqtt was not updating while lamp was on)
                        logging.error(
                            "   {}: Return value \"{}\" not matching requested value \"{}\". Unipi might not be responding or in error. Retuning mqtt message with actual level, not requested".format(
                                get_function_name(), round(actual_level['value'], 1), desired_level))
                        temp_level = math.ceil(actual_level['value'] * 25.5)
                        message.update({
                            "brightness": temp_level})  # replace requested level with actual level in orderd dict action
                        mqtt_ack(topic, message)
                    else:
                        execution_error == 0
                        logging.info(
                            '   {}: Return value "{}" IS matching requested value "{}". Proceeding in compiling the MQTT message to ack that.'.format(
                                get_function_name(), round(actual_level['value'], 1), desired_level))
                    if execution_error != 1:
                        # COMPILE THE MQTT ACK MESSAGE TO HASSIO
                        mqtt_ack(topic, message)
                        logging.info(
                            '    {}: Finished Set brightness for dev "{}" circuit "{}" to "{}" in "{}" seconds.'.format(
                                get_function_name(), dev, circuit, desired_brightness, trans_time))
                else:
                    logging.error('   {}: Unhandled Condition'.format(get_function_name()))
            else:  # handled when thread finishes by completion or external stop signal (StopThread function)
                if thread.is_stopping():
                    logging.info(
                        '   {}: Thread {} was given stop signal and stop before finish. Leaving the cleaning of thread information to "def StopThread". NOT sending final MQTT messages'.format(
                            get_function_name(), thread_id))
                else:
                    logging.warning(
                        '   {}: Successful Finished thread {}, now deleting thread information from global thread var'.format(
                            get_function_name(), thread_id))
                    del dThreads[thread_id]
                logging.debug('   {}: EOF.'.format(get_function_name()))
            short_lived_ws.close()  # Closing the websocket connection for this function and interation.
        else:
            logging.error('    {}: delta_level != number_steps.'.format(get_function_name(), dev, circuit))
    else:
        logging.info(
            '    {}: Actual UniPi status for device {} circuit {} is matching desired state, not changing anything.'.format(
                get_function_name(), dev, circuit))


### UniPi outputs Switch Commands
### Used to switch outputs on the UniPi device based on the websocket message received

def off_commands():
    # Function to handle delayed off for devices based on config file. use to switch motion sensors off (get a pulse update every 10 sec)
    tijd = time.time()
    for config_dev in devdes:
        device_type_presence = 'device_type' in config_dev
        handle_local_presence = 'handle_local' in config_dev
        if (device_type_presence == True):
            if (config_dev[
                'device_type'] == 'counter'):  # need this to set counter to 0 via MQTT otherwise only messages with a value are send
                if (tijd >= (config_dev['unipi_prev_value_timstamp'] + config_dev['device_delay'])):
                    if ((config_dev['counter_value'] >= config_dev['unipi_value']) and (
                            config_dev['counter_value'] > 0)):
                        counter = config_dev["counter_value"]
                        delta = config_dev["counter_value"] - config_dev[
                            "unipi_value"]  # abuse of unipi value, but since we dont use this for counter devices...
                        config_dev["unipi_value"] = config_dev["counter_value"]
                        config_dev['unipi_prev_value_timstamp'] = tijd
                        if counter != delta:
                            mqtt_set_counter(config_dev["state_topic"], counter, delta)
                        else:
                            logging.warning(
                                '{}: counter ({}) has the same value as ({}), not sending MQTT as this is startup error that I need to fix.'.format(
                                    get_function_name(), counter, delta))
                    elif config_dev['counter_value'] == 0:
                        pass  # this happens on boot with 0 as value untill the first counter values come in.
                    else:
                        logging.error('{}: Negative value!.'.format(get_function_name()))
                        logging.error('{}:  - config: {}'.format(get_function_name(), config_dev))
            else:
                logging.error(
                    '{}: Unknown device type "{}", breaking.'.format(get_function_name(), config_dev['device_type']))
        elif 'device_delay' in config_dev:  # Only switch devices off that have a delay > 0. Devices with no delay or delay '0' do not need to turned off or are turned off bij a new status (like door sensor)
            if config_dev['device_delay'] > 0 and tijd >= (
                    config_dev['unipi_prev_value_timstamp'] + config_dev['device_delay']):
                # dev_switch_off(config_dev['state_topic']) #device uit zetten
                # if config_dev['unipi_value'] == 1 and config_dev['device_normal'] == 'no':
                if config_dev['unipi_value'] == 1 and config_dev['device_normal'] == 'no':
                    publish_state(config_dev['state_topic'], payload_off)
                    if handle_local_presence == True: handle_local_switch_toggle(message_dev, config_dev)
                    config_dev['unipi_value'] = 0  # Set var in config file to off
                    logging.info(
                        '{}: Triggered delayed OFF after {} sec for "no" device "{}" for MQTT topic: "{}" .'.format(
                            get_function_name(), config_dev['device_delay'], config_dev['description'],
                            config_dev['state_topic']))
                elif config_dev['unipi_value'] == 0 and config_dev['device_normal'] == 'nc':
                    publish_state(config_dev['state_topic'], payload_off)
                    if handle_local_presence == True: handle_local_switch_toggle(message_dev, config_dev)
                    config_dev['unipi_value'] = 1  # Set var in config file to on
                    logging.info(
                        '{}: Triggered delayed OFF after {} sec for "nc" device "{}" for MQTT topic: "{}" .'.format(
                            get_function_name(), config_dev['device_delay'], config_dev['description'],
                            config_dev['state_topic']))
            # else:
            #	logging.debug('{}: unhandled exception in device switch off'.format(get_function_name()))
    logging.debug('   {}: EOF.'.format(get_function_name()))


def publish_state(topic: str, state: str):
    mqttc.publish(topic, payload=state, qos=1, retain=True)
    logging.info('Set {} for MQTT topic: "{}".'.format(state, topic))


def mqtt_set_lux(mqtt_topic, lux):
    try:
        send_msg = {
            "lux": lux
        }
        mqttc.publish(mqtt_topic, payload=json.dumps(send_msg), qos=1, retain=False)
        logging.info('{}: Set LUX: {} for MQTT topic: "{}" .'.format(get_function_name(), lux, mqtt_topic))
    except:
        logging.error(
            '{}: An error has occurred sending "{}" C for MQTT topic: "{}" .'.format(get_function_name(), lux,
                                                                                     mqtt_topic))


def mqtt_set_temp(mqtt_topic, temp):
    try:
        send_msg = {
            "temperature": temp
        }
        mqttc.publish(mqtt_topic, payload=json.dumps(send_msg), qos=1, retain=False)
        logging.info('{}: Set temperature: {} C for MQTT topic: "{}" .'.format(get_function_name(), temp, mqtt_topic))
    except:
        logging.error(
            '{}: An error has occurred sending "{}" C for MQTT topic: "{}" .'.format(get_function_name(), temp,
                                                                                     mqtt_topic))


def mqtt_set_humi(mqtt_topic, humi):
    try:
        send_msg = {
            "humidity": humi
        }
        mqttc.publish(mqtt_topic, payload=json.dumps(send_msg), qos=1, retain=False)
        logging.info('{}: Set humidity: {} for MQTT topic: "{}" .'.format(get_function_name(), humi, mqtt_topic))
    except:
        logging.error(
            '{}: An error has occurred sending "{}" C for MQTT topic: "{}" .'.format(get_function_name(), humi,
                                                                                     mqtt_topic))


def mqtt_set_counter(mqtt_topic, counter,
                     delta):  # published an MQTT message with a counter delta based on the interval defined or between de messages received. Messages from webdav might not trigger every pulse.
    logging.debug('Hit Functions {}'.format(get_function_name()))
    send_msg = {
        "counter_delta": delta,
        "counter": counter
    }
    mqttc.publish(mqtt_topic, payload=json.dumps(send_msg), qos=1, retain=False)
    logging.info(
        '{}: Set counter {} and delta: {} for topic "{}" .'.format(get_function_name(), counter, delta, mqtt_topic))


def mqtt_topic_ack(mqtt_topic, mqtt_message):
    mqttc.publish(mqtt_topic, payload=mqtt_message, qos=1, retain=False)
    logging.info(
        '{}: Send MQTT message: "{}" for MQTT topic: "{}" .'.format(get_function_name(), mqtt_message, mqtt_topic))


def mqtt_topic_set(mqtt_topic, mqtt_message):
    mqtt_topic = mqtt_topic + "/set"
    mqttc.publish(mqtt_topic, payload=mqtt_message, qos=1,
                  retain=True)  # changed retain to true as HASS does a retain true for most messages. Meaning actual state is not maintained to last resort.
    logging.info(
        '{}: Send MQTT message: "{}" for MQTT topic: "{}" .'.format(get_function_name(), mqtt_message, mqtt_topic))


### Handle Local Switch Commands
### Used to switch local outputs based on the websock input with some basic logic so some stuff still works when we do not have a working MQTT / Home Assistant

def handle_local_switch_on_or_toggle(message_dev, config_dev):
    logging.debug(
        '{}: Handle Local ON for message: {} and handle_local_config {}.'.format(get_function_name(), message_dev,
                                                                                 config_dev["handle_local"]))
    if config_dev["handle_local"]["type"] == 'bel':
        unipy.ring_bel(config_dev["handle_local"]["rings"], "relay", config_dev["handle_local"]["output_circuit"])
        logging.info('{}: Handle Local is ringing the bel {} times'.format(get_function_name(),
                                                                           config_dev["handle_local"]["rings"]))
        mqtt_message = 'ON'
        mqtt_topic_ack(config_dev["state_topic"],
                       mqtt_message)  # (we send a set too, to maks sure we stop threads in mqtt_client)
        mqtt_message = 'OFF'
        mqtt_topic_ack(config_dev["state_topic"],
                       mqtt_message)  # (we send a set too, to maks sure we stop threads in mqtt_client)
    else:
        handle_local_switch_toggle(message_dev, config_dev)


def handle_local_switch_toggle(message_dev, config_dev):
    logging.debug('{}: Starting function with message "{}"'.format(get_function_name(), message_dev))
    if config_dev["handle_local"]["type"] == 'dimmer':
        logging.debug('{}: Dimmer Toggle Running.'.format(get_function_name()))
        status, success = (unipy.toggle_dimmer("analogoutput", config_dev["handle_local"]["output_circuit"],
                                               config_dev["handle_local"]["level"]))
        # unipy.toggle_dimmer('analogoutput', '2_03', 7)
        if success == 200:  # I know, mixing up status and succes here from the unipython class... some day ill fix it.
            if status == 0:
                mqtt_message = '{"state": "off", "circuit": "' + config_dev["handle_local"][
                    "output_circuit"] + '", "dev": "analogoutput"}'
                mqtt_topic_set(config_dev["state_topic"],
                               mqtt_message)  # (we send a set too, to maks sure we stop threads in mqtt_client)
                logging.info('{}: Handle Local toggled analogoutput {} to OFF'.format(get_function_name(),
                                                                                      config_dev["handle_local"][
                                                                                          "output_circuit"]))
            elif status == 1:
                brightness = math.ceil(config_dev["handle_local"]["level"] * 25.5)
                mqtt_message = '{"state": "on", "circuit": "' + config_dev["handle_local"][
                    "output_circuit"] + '", "dev": "analogoutput", "brightness": ' + str(brightness) + '}'
                mqtt_topic_set(config_dev["state_topic"],
                               mqtt_message)  # (we send a set too, to maks sure we stop threads in mqtt_client)
                logging.info('{}: Handle Local toggled analogoutput {} to ON'.format(get_function_name(),
                                                                                     config_dev["handle_local"][
                                                                                         "output_circuit"]))
            elif (status == 666 or status == 667):
                logging.error(
                    '{}: Received error from rest call with code "{}" on analogoutput {}.'.format(get_function_name(),
                                                                                                  status, config_dev[
                                                                                                      "handle_local"][
                                                                                                      "output_circuit"]))
            else:
                logging.error(
                    '{}: "status" not 0,1,666 or 667 while running "dimmer loop"."'.format(get_function_name()))
        else:
            logging.error('{}: Tried to toggle analogoutput {} but failed with http return code "{}" .'.format(
                get_function_name(), config_dev["handle_local"]["output_circuit"], success))
    elif config_dev["handle_local"]["type"] == 'switch':
        logging.debug('Switch Toggle Running function : "{}"'.format(get_function_name()))
        status, success = (unipy.toggle_switch("output", config_dev["handle_local"]["output_circuit"]))
        if success == 200:
            if status == 0:
                # mqtt_message = 'OFF' #used this for simple MQTT ack message, but looks like I don't use this, so changing to more advanced json MQTT message. This mist match payload_on / off messages!
                mqtt_message = '{"state": "off", "circuit": "' + config_dev["handle_local"][
                    "output_circuit"] + '", "dev": "output"}'
                mqtt_topic_set(config_dev["state_topic"],
                               mqtt_message)  # (we send a set too, to maks sure we stop threads in mqtt_client)
                logging.info('{}: Handle Local toggled output {} to OFF'.format(get_function_name(),
                                                                                config_dev["handle_local"][
                                                                                    "output_circuit"]))
            elif status == 1:
                # mqtt_message = 'ON' #used this for simple MQTT ack message, but looks like I don't use this, so changing to more advanced json MQTT message. This mist match payload_on / off messages at HA to work / show status there.
                mqtt_message = '{"state": "on", "circuit": "' + config_dev["handle_local"][
                    "output_circuit"] + '", "dev": "output"}'
                mqtt_topic_set(config_dev["state_topic"],
                               mqtt_message)  # (we send a set too, to maks sure we stop threads in mqtt_client)
                logging.info('{}: Handle Local toggled output {} to ON'.format(get_function_name(),
                                                                               config_dev["handle_local"][
                                                                                   "output_circuit"]))
            elif (status == 666 or status == 667):
                logging.error(
                    '{}: Received error from rest call with code "{}" on output {}.'.format(get_function_name(), status,
                                                                                            config_dev["handle_local"][
                                                                                                "output_circuit"]))
            else:
                logging.error('{}: "status" not found while running "switch loop"'.format(get_function_name()))
        else:
            logging.error(
                "{}: Tried to toggle device  {} but failed with http return code '{}' .".format(get_function_name(),
                                                                                                config_dev[
                                                                                                    "handle_local"][
                                                                                                    "output_circuit"],
                                                                                                success))
    else:
        logging.error('{}: Unhandled exception in function config type: {}'.format(get_function_name(),
                                                                                   config_dev["handle_local"]["type"]))
    logging.debug('{}: EOF.'.format(get_function_name()))


### MQTT CONNECTION FUNCTIONS ###

def mqtt_ack(topic, message):
    # Function to adjust MQTT message / topic to return to sender.
    logging.debug(
        '         {}: Starting function on topic "{}" with message "{}".'.format(get_function_name(), topic, message))
    if topic.endswith('/set'):
        topic = topic[:-4]
        logging.debug('         {}: Removed "set" from state topic, is now "{}" .'.format(get_function_name(), topic))
    if topic.endswith('/brightness'):
        topic = topic[:-11]
        logging.debug(
            '         {}: Removed "/brightness" from state topic, is now "{}" .'.format(get_function_name(), topic))
    # Adjusting Message to be returned
    if 'mqtt_reply_message' in message:
        # this is currently unused, not a clue why i build it once...
        logging.debug('         {}:Found "mqtt_reply_message" key in message "{}", changing reply message.'.format(
            get_function_name(), message))
        for key, value in message.items():
            if key == 'mqtt_reply_message':
                message = value
                logging.debug('         {}:Message set to: "{}".'.format(get_function_name(), message))
    else:
        logging.debug('         {}:UNchanged return message, remains "{}" .'.format(get_function_name(), message))
    # returnmessage = message
    return_message = json.dumps(
        message)  # we need this due to the fact that some MQTT message need a retun value of ON or OFF instead of original message
    mqttc.publish(topic, return_message, qos=0,
                  retain=True)  # You need to confirm light status to leave it on in HASSIO
    logging.debug(
        '         {}: Returned topic is "{}" and message is "{}".'.format(get_function_name(), return_message, topic))
    logging.debug('         {}: EOF.'.format(get_function_name()))


# The callback for when the client receives a CONNACK response from the server.
def on_mqtt_connect(mqttc, userdata, flags, rc):
    logging.info('{}: MQTT Connected with result code {}.'.format(get_function_name(), str(rc)))
    mqttc.subscribe(
        mqtt_subscr_topic)  # Subscribing in on_connect() means that if we lose the connection and reconnect then subscriptions will be renewed.
    mqtt_online()


def mqtt_online():  # function to bring MQTT devices online to broker
    for dd in devdes:
        mqtt_topic_online = (dd['state_topic'] + "/available")
        mqttc.publish(mqtt_topic_online, payload='online', qos=2, retain=True)
        logging.info('{}: MQTT "online" command to topic "{}" send.'.format(get_function_name(), mqtt_topic_online))


def on_mqtt_subscribe(mqttc, userdata, mid, granted_qos):
    logging.info(
        '{}: Subscribed with details: mqttc: {}, userdata: {}, mid: {}, granted_qos: {}.'.format(get_function_name(),
                                                                                                 mqttc, userdata, mid,
                                                                                                 granted_qos))


def on_mqtt_disconnect(mqttc, userdata, rc):
    logging.critical('{}: MQTT DISConnected from MQTT broker with reason: {}.'.format(get_function_name(),
                                                                                      str(rc)))  # Return Code (rc)- Indication of disconnect reason. 0 is normal all other values indicate abnormal disconnection
    if str(rc) == 0:
        mqttc.unsubscribe(mqtt_subscr_topic)
        mqtt_offline()


def mqtt_offline():  # function to bring MQTT devices offline to broker
    for dd in devdes:
        # print("debug2")
        mqtt_topic_offline = (dd['state_topic'] + "/available")
        mqttc.publish(mqtt_topic_offline, payload='offline', qos=0, retain=True)
        logging.warning(
            '{}: MQTT "offline" command to topic "{}" send.'.format(get_function_name(), mqtt_topic_offline))
    mqttc.disconnect()


def on_mqtt_unsubscribe(mqttc, userdata, mid, granted_qos):
    logging.info(
        '{}: Unsubscribed with details: mqttc: {}, userdata: {}, mid: {}, granted_qos: {}.'.format(get_function_name(),
                                                                                                   mqttc, userdata, mid,
                                                                                                   granted_qos))


def on_mqtt_close(ws):
    logging.warning('{}: MQTT on_close function called.'.format(get_function_name()))


def on_mqtt_log(client, userdata, level, buf):
    logging.debug('{}: {}'.format(get_function_name(), buf))


### WEBSOCKET CONNECTION FUNCTIONS ###

def create_ws():
    while True:
        try:
            websocket.enableTrace(False)
            ws = websocket.WebSocketApp("ws://" + ws_server + ":1080/ws",  # header=ws_header,
                                        on_open=on_ws_open,
                                        on_message=on_ws_message,
                                        on_error=on_ws_error,
                                        on_close=on_ws_close)
            ws.run_forever(skip_utf8_validation=True, ping_interval=10, ping_timeout=8)  # open websocket connection
        except Exception as e:
            gc.collect()
            logging.error("Websocket connection Error  : {0}".format(e))
        logging.error("Reconnecting websocket  after 5 sec")
        time.sleep(5)  # sleep to prevent setting up many connections / sec.


def on_ws_open(ws):
    logging.error('{}: WebSockets connection is starting in a separate thread!'.format(get_function_name()))
    firstrun()


# TODO, Build a first run function to set ACTUAL states of UniPi inputs as MQTT message and in config file!

def on_ws_message(ws, message):
    ws_sanity_check(message)  # This is starting the main message handling for UniPi originating messages


# print(ws)
# print(message)

def on_ws_close(ws):
    logging.critical(
        '{}: WEBSOCKETS CONNECTION CLOSED - THIS WILL PREVENT UNIPI INITIATED ACTIONS FROM RUNNING!'.format(
            get_function_name()))
    if ws.isAlive():
        ws.join()
        logging.error('{}: Joined websocket thread into main thread to cleanup thread.'.format(get_function_name()))
    else:
        logging.error('{}: WebSockets thread was not foundrunning, in reconnect loop?'.format(get_function_name()))


def on_ws_error(ws, errors):
    logging.error('{}: WebSocket Error; "{}"'.format(get_function_name(), errors))


#	for line in traceback.format_stack():
#		logging.error(line)

### First Run Function to set initial state of Inputs
def firstrun():
    for config_dev in devdes:
        message = unipy.get_circuit(config_dev['dev'], config_dev['circuit'])
        try:
            message = json.dumps(message)
            logging.info('{}: Set status for dev: {}, circuit: {} to message and values: {}'.format(get_function_name(),
                                                                                                    config_dev['dev'],
                                                                                                    config_dev[
                                                                                                        'circuit'],
                                                                                                    message))
            ws_sanity_check(message)
        except:
            logging.error(
                '{}: Input error in first run, message received is ERROR {} on dev: {} and circuit: {}. Please ignore if dev humidity or light'.format(
                    get_function_name(), message, config_dev['dev'], config_dev['circuit']))
        # Note first run will also find dev = humidity, etc. but cannot match that to a get to unipi and the creates arror 500, however the humidity is already handled on topic "temp" as humidity is not a device class. Maybe oneday clean this up by changing dev types and something like sub_dev, but works like a charm this way too.
        # Pre-empt the dicts with values and an array to fill in the counter values and sensor values to calculate an average value for sensors.
        # We only do this for sensors where we find "interval" in the configuration file. Since we start with 0, 0=1, 1=2, etc.
        int_presence = 'interval' in config_dev
        if (int_presence == True):
            global intervals_average
            global intervals_counter
            intervals_average[(config_dev['dev'] + config_dev['circuit'])] = [0.0] * (config_dev['interval'] + 1)
            intervals_counter[(config_dev['dev'] + config_dev['circuit'])] = 0


if __name__ == "__main__":
    # setting some housekeeping functions and globel vars
    logging.basicConfig(format='%(asctime)s:%(levelname)s:%(message)s', filename=logging_path, level=logging.DEBUG,
                        datefmt='%Y-%m-%d %H:%M:%S')  # DEBUG,INFO,WARNING,ERROR,CRITICAL
    # ignoring informational logging from called modules (rest calls in this case)
    # https://stackoverflow.com/questions/24344045/how-can-i-completely-remove-any-logging-from-requests-module-in-python
    urllib3_log = logging.getLogger("urllib3")
    urllib3_log.setLevel(logging.CRITICAL)
    unipy = unipython(ws_server, ws_user, ws_pass)

    # Loading the JSON settingsfile
    dirname = os.path.dirname(__file__)  # set relative path for loading files
    dev_des_file = os.path.join(dirname, 'unipi_mqtt_devices.json')
    devdes: List[dict] = json.load(open(dev_des_file))

    # MQTT Connection.
    mqttc = mqtt.Client(
        mqtt_client_name)  # If you want to use a specific client id, use this, otherwise a random id is generated.
    mqttc.on_connect = on_mqtt_connect
    mqttc.on_log = on_mqtt_log  # set client logging
    mqttc.on_disconnect = on_mqtt_disconnect
    mqttc.on_subscribe = on_mqtt_subscribe
    mqttc.on_unsubscribe = on_mqtt_unsubscribe
    mqttc.on_message = on_mqtt_message
    mqttc.username_pw_set(username=mqtt_user, password=mqtt_pass)
    mqttc.connect(mqtt_address, 1883, 600, )  # define MQTT server settings
    t_mqtt = threading.Thread(target=mqttc.loop_forever)  # define a thread to run MQTT connection
    t_mqtt.start()  # Start connection to MQTT in thread so non-blocking

    # WebSocket listener Connection. Must be in main to be referenced from other functions like ws.send,
    # so we handle this differently since I moved this to a function. start a function, so we can reconnect on
    # disconnect (like EVOK upgrade or network outage) every 5 seconds starts in a separate thread to not block
    # anything

    t_websocket = threading.Thread(target=create_ws)  # define a thread to run MQTT connection
    t_websocket.start()  # Start connection to MQTT in thread so non-blocking

    # Time function, so we're not dependent of incoming commands to trigger things
    # https://stackoverflow.com/questions/474528/what-is-the-best-way-to-repeatedly-execute-a-function-every-x-seconds
    threading.Thread(target=lambda: every(1, off_commands)).start()
