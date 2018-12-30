#!/usr/bin/env python

"""
The MIT License (MIT)

Copyright (c) 2017 Johan Kanflo (github.com/kanflo)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

This script is used to communicate with an OpenDPS device and can be used to
change all the settings possible with the buttons and dial on the device
directly. The device can be talked to via a serial interface or (if you added
an ESP8266) via wifi

dpsctl.py --help will provide enlightment.

Oh, and if you get tired of specifying the comms interface (TTY or IP) all the
time, add it tht the environment variable DPSIF.

"""

from __future__ import print_function
import argparse
import sys
import os
import socket
try:
    import serial
except:
    print("Missing dependency pyserial:")
    print(" sudo pip%s install pyserial" % ("3" if sys.version_info.major == 3 else ""))
    raise SystemExit()
import threading
import time
from uhej import uhej
from protocol import *
import uframe
import binascii
try:
    from PyCRC.CRCCCITT import CRCCCITT
except:
    print("Missing dependency pycrc:")
    print(" sudo pip%s install pycrc" % ("3" if sys.version_info.major == 3 else ""))
    raise SystemExit()
import json

parameters = []

"""
An abstract class that describes a communication interface
"""
class comm_interface(object):

    _if_name = None

    def __init__(self, if_name):
        self._if_name = if_name

    def open(self):
        return False

    def close(self):
        return False

    def write(self, bytes):
        return False

    def read(self):
        return bytearray()

    def name(self):
        return self._if_name

"""
A class that describes a serial interface
"""
class tty_interface(comm_interface):

    _port_handle = None

    def __init__(self, if_name):
        self._if_name = if_name

    def open(self):
        self._port_handle = serial.Serial(baudrate = 115200, timeout = 1.0)
        self._port_handle.port = self._if_name
        self._port_handle.open()
        return True

    def close(self):
        self._port_handle.port.close()
        self._port_handle = None
        return True

    def write(self, bytes):
        self._port_handle.write(bytes)
        return True

    def read(self):
        bytes = bytearray()
        sof = False
        while True:
            b = self._port_handle.read(1)
            if not b: # timeout
                break
            b = ord(b)
            if b == uframe._SOF:
                bytes = bytearray()
                sof = True
            if sof:
                bytes.append(b)
            if b == uframe._EOF:
                break
        return bytes

"""
A class that describes a UDP interface
"""
class udp_interface(comm_interface):

    _socket = None

    def __init__(self, if_name):
        self._if_name = if_name

    def open(self):
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.settimeout(1.0)
        except socket.error:
            return False
        return True

    def close(self):
        self._socket.close()
        self._socket = None
        return True

    def write(self, bytes):
        try:
            self._socket.sendto(bytes, (self._if_name, 5005))
        except socket.error as msg:
            fail("%s (%d)" % (str(msg[0]), msg[1]))
        return True

    def read(self):
        reply = bytearray()
        try:
            d = self._socket.recvfrom(1000)
            reply = bytearray(d[0])
            addr = d[1]
        except socket.timeout:
            pass
        except socket.error:
            pass
        return reply

"""
Print error message and exit with error
"""
def fail(message):
        print("Error: %s." % (message))
        sys.exit(1)


"""
Return name of unit (must of course match unit_t in opendps/uui.h)
"""
def unit_name(unit):
    if unit == 0: return "A"
    if unit == 1: return "V"
    if unit == 2: return "W"
    if unit == 3: return "s"
    if unit == 4: return "Hz"
    return "unknown"

"""
Return SI prefix
"""
def prefix_name(prefix):
    if prefix == -6: return "u"
    if prefix == -3: return "m"
    if prefix == -2: return "c"
    if prefix == -1: return "d" # TODO: is this correct (deci?
    if prefix ==  0: return ""
    if prefix ==  1: return "D" # TODO: is this correct (deca)?
    if prefix ==  2: return "hg"
    if prefix ==  3: return "k"
    if prefix ==  4: return "M"
    return "e%d" % prefix

"""
Handle a response frame from the device.
Return a dictionaty of interesting information.
"""
def handle_response(command, frame, args, quiet=False):
    ret_dict = {}
    resp_command = frame.get_frame()[0]
    if resp_command & cmd_response:
        resp_command ^= cmd_response
        success = frame.get_frame()[1]
        if resp_command != command:
            print("Warning: sent command %02x, response was %02x." % (command, resp_command))
        if resp_command !=  cmd_upgrade_start and resp_command != cmd_upgrade_data and not success:
            fail("command failed according to device")

    if args.json:
        _json = {}
        _json["cmd"] = resp_command;
        _json["status"] = 1; # we're here aren't we?

    if resp_command == cmd_ping:
        print("Got pong from device")
    elif resp_command == cmd_cal_report:
        ret_dict = unpack_cal_report(frame)
    elif resp_command == cmd_query:
        ret_dict = unpack_query_response(frame)
        enable_str = "on" if ret_dict['output_enabled'] else "temperature shutdown" if ret_dict['temp_shutdown'] == 1 else "off"
        v_in_str = "%d.%02d" % (ret_dict['v_in']/1000, (ret_dict['v_in']%1000)/10)
        v_out_str = "%d.%02d" % (ret_dict['v_out']/1000, (ret_dict['v_out']%1000)/10)
        i_out_str = "%d.%03d" % (ret_dict['i_out']/1000, ret_dict['i_out']%1000)
        if args.json:
            _json = ret_dict
        else:
            if not quiet:
                print("%-10s : %s (%s)" % ('Func', ret_dict['cur_func'], enable_str))
                for key, value in ret_dict['params'].iteritems():
                    print("  %-8s : %s" % (key, value))
                print("%-10s : %s V" % ('V_in', v_in_str))
                print("%-10s : %s V" % ('V_out', v_out_str))
                print("%-10s : %s A" % ('I_out', i_out_str))
                if 'temp1' in ret_dict:
                    print("%-10s : %.1f" % ('temp1', ret_dict['temp1']))
                if 'temp2' in ret_dict:
                    print("%-10s : %.1f" % ('temp2', ret_dict['temp2']))

    elif resp_command == cmd_upgrade_start:
    #  *  DPS BL: [cmd_response | cmd_upgrade_start] [<upgrade_status_t>] [<chunk_size:16>]
        cmd = frame.unpack8()
        status = frame.unpack8()
        chunk_size = frame.unpack16()
        ret_dict["status"] = status
        ret_dict["chunk_size"] = chunk_size
    elif resp_command == cmd_upgrade_data:
        cmd = frame.unpack8()
        status = frame.unpack8()
        ret_dict["status"] = status
    elif resp_command == cmd_set_function:
        cmd = frame.unpack8()
        status = frame.unpack8()
        if not quiet:
            if not status:
                print("Function does not exist.") # Never reached due to status == 0
            else:
                print("Changed function.")
    elif resp_command == cmd_list_functions:
        cmd = frame.unpack8()
        status = frame.unpack8()
        if status == 0:
            print("Error, failed to list available functions")
        else:
            functions = []
            name = frame.unpack_cstr()
            while name != "":
                functions.append(name)
                name = frame.unpack_cstr()
            if args.json:
                _json["functions"] = functions;
            else:
                if len(functions) == 0:
                    print("Selected OpenDPS supports no functions at all, which is quite weird when you think about it...")
                elif len(functions) == 1:
                    print("Selected OpenDPS supports the %s function." % functions[0])
                else:
                    temp = ", ".join(functions[:-1])
                    temp = "%s and %s" % (temp, functions[-1])
                    print("Selected OpenDPS supports the %s functions." % temp)
    elif resp_command == cmd_set_parameters:
        cmd = frame.unpack8()
        status = frame.unpack8()
        for p in args.parameter:
            status = frame.unpack8()
            parts = p.split("=")
            # TODO: handle json output
            if not quiet:
                print("%s: %s" % (parts[0], "ok" if status == 0 else "unknown parameter" if status == 1 else "out of range" if status == 2 else "unsupported parameter" if status == 3 else "unknown error %d" % (status)))
    elif resp_command == cmd_list_parameters:
        cmd = frame.unpack8()
        status = frame.unpack8()
        if status == 0:
            print("Error, failed to list available parameters")
        else:
            cur_func = frame.unpack_cstr()
            parameters = []
            while not frame.eof():
                parameter = {}
                parameter['name'] = frame.unpack_cstr()
                parameter['unit'] = unit_name(frame.unpack8())
                parameter['prefix'] = prefix_name(frame.unpacks8())
                parameters.append(parameter)
            if args.json:
                _json["current_function"] = cur_func;
                _json["parameters"] = parameters
            else:
                if len(parameters) == 0:
                    print("Selected OpenDPS supports no parameters at all for the %s function" % (cur_func))
                elif len(parameters) == 1:
                    print("Selected OpenDPS supports the %s parameter (%s%s) for the %s function." % (parameters[0]['name'], parameters[0]['prefix'], parameters[0]['unit'], cur_func))
                else:
                    temp = ""
                    for p in parameters:
                        temp += p['name'] + ' (%s%s)' % (p['prefix'], p['unit']) + " "
                    print("Selected OpenDPS supports the %sparameters for the %s function." % (temp, cur_func))
    elif resp_command == cmd_enable_output:
        cmd = frame.unpack8()
        status = frame.unpack8()
        if status == 0:
            print("Error, failed to enable/disable output.")
    elif resp_command == cmd_temperature_report:
        pass
    elif resp_command == cmd_lock:
        pass
    elif resp_command == cmd_set_calibration:
        pass
    elif resp_command == cmd_init:
        pass
    else:
        print("Unknown response %d from device." % (resp_command))

    if args.json:
        print(json.dumps(_json, indent=4, sort_keys=True))

    return ret_dict

"""
Communicate with the DPS device according to the user's whishes
"""
def communicate(comms, frame, args, quiet=False):
    bytes = frame.get_frame()

    if not comms:
        fail("no communication interface specified")
    if not comms.open():
        fail("could not open %s" % (comms.name()))
    if args.verbose:
        print("Communicating with %s" % (comms.name()))
        print("TX %2d bytes [%s]" % (len(bytes), " ".join("%02x" % b for b in bytes)))
    if not comms.write(bytes):
        fail("write failed on %s" % (comms.name()))
    resp = comms.read()
    if len(resp) == 0:
        fail("timeout talking to device %s" % (comms._if_name))
    elif args.verbose:
        print("RX %2d bytes [%s]\n" % (len(resp), " ".join("%02x" % b for b in resp)))
    if not comms.close:
        print("Warning: could not close %s" % (comms.name()))

    f = uFrame()
    res = f.set_frame(resp)
    if res < 0:
        fail("protocol error (%d)" % (res))
    else:
        return handle_response(frame.get_frame()[1], f, args, quiet)

"""
Communicate with the DPS device according to the user's whishes
"""
def handle_commands(args):
    if args.scan:
        uhej_scan()
        return

    comms = create_comms(args)
    if args.init:
        communicate(comms,create_cmd(cmd_init),args)
        
    if args.ping:
        communicate(comms, create_cmd(cmd_ping), args)

    if args.firmware:
        run_upgrade(comms, args.firmware, args)

    if args.lock:
        communicate(comms, create_lock(1), args)
        
    if args.unlock:
        communicate(comms, create_lock(0), args)

    if args.list_functions:
        communicate(comms, create_cmd(cmd_list_functions), args)

    if args.list_parameters:
        communicate(comms, create_cmd(cmd_list_parameters), args)

    if args.function:
        communicate(comms, create_set_function(args.function), args)

    if args.enable:
        if args.enable == 'on' or args.enable == 'off':
            communicate(comms, create_enable_output(args.enable), args)
        else:
            fail("enable is 'on' or 'off'")
            
    if args.calibration_args:
        payload = create_set_calibration(args.calibration_args)
        if payload:
            communicate(comms,payload,args)
        else:
            fail("malformatted parameters")
    if args.calibration_report:
        data = communicate(comms, create_cmd(cmd_cal_report), args)
        print("Calibration Report:")
        print("\tA_ADC_K = {}".format(data['cal']['A_ADC_K']))
        print("\tA_ADC_C = {}".format(data['cal']['A_ADC_C']))
        print("\tA_DAC_K = {}".format(data['cal']['A_DAC_K']))
        print("\tA_DAC_C = {}".format(data['cal']['A_DAC_C']))
        print("\tV_ADC_K = {}".format(data['cal']['V_ADC_K']))
        print("\tV_ADC_C = {}".format(data['cal']['V_ADC_C']))
        print("\tV_DAC_K = {}".format(data['cal']['V_DAC_K']))
        print("\tV_DAC_C = {}".format(data['cal']['V_DAC_C']))
        print("\tVIN_ADC_K = {}".format(data['cal']['VIN_ADC_K']))
        print("\tVIN_ADC_C = {}".format(data['cal']['VIN_ADC_C']))
        print("\tVIN_ADC = {}".format(data['vin_adc']))
        print("\tVOUT_ADC = {}".format(data['vout_adc']))
        print("\tIOUT_ADC = {}".format(data['iout_adc']))
        print("\tIOUT_DAC = {}".format(data['iout_dac']))
        print("\tVOUT_DAC = {}".format(data['vout_dac']))
        
    if args.parameter:
        payload = create_set_parameter(args.parameter)
        if payload:
            communicate(comms, payload, args)
        else:
            fail("malformatted parameters")

    if args.query:
        communicate(comms, create_cmd(cmd_query), args)

    if hasattr(args, 'temperature') and args.temperature:
        communicate(comms, create_temperature(float(args.temperature)), args)
        
    if args.calibrate:
        do_calibration(comms,args)

"""
Return True if the parameter if_name is an IP address.
"""
def is_ip_address(if_name):
    try:
        socket.inet_aton(if_name)
        return True
    except socket.error:
        return False

# Darn beautiful, from SO: https://stackoverflow.com/a/1035456
def chunk_from_file(filename, chunk_size):
    with open(filename, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if chunk:
                yield bytearray(chunk)
            else:
                break

"""
Run OpenDPS firmware upgrade
"""
def run_upgrade(comms, fw_file_name, args):
    with open(fw_file_name, mode='rb') as file:
        #crc = binascii.crc32(file.read()) % (1<<32)
        content = file.read()
        if content.encode('hex')[6:8] != "20" and not args.force:
            fail("The firmware file does not seem valid, use --force to force upgrade")
        crc = CRCCCITT().calculate(content)
    chunk_size = 1024
    ret_dict = communicate(comms, create_upgrade_start(chunk_size, crc), args)
    if ret_dict["status"] == upgrade_continue:
        if chunk_size != ret_dict["chunk_size"]:
            print("Device selected chunk size %d" % (ret_dict["chunk_size"]))
        counter = 0
        for chunk in chunk_from_file(fw_file_name, chunk_size):
            counter += len(chunk)
            sys.stdout.write("\rDownload progress: %d%% " % (counter*1.0/len(content)*100.0) )
            sys.stdout.flush()
#            print(" %d bytes" % (counter))

            ret_dict = communicate(comms, create_upgrade_data(chunk), args)
            status = ret_dict["status"]
            if status == upgrade_continue:
                pass
            elif status == upgrade_crc_error:
                print("")
                fail("device reported CRC error")
            elif status == upgrade_erase_error:
                print("")
                fail("device reported erasing error")
            elif status == upgrade_flash_error:
                print("")
                fail("device reported flashing error")
            elif status == upgrade_overflow_error:
                print("")
                fail("device reported firmware overflow error")
            elif status == upgrade_success:
                print("")
            else:
                print("")
                fail("device reported an unknown error (%d)" % status)
    else:
        fail("Device rejected firmware upgrade")
    sys.exit(os.EX_OK)

"""
Calculate linear line of best fit's constants
"""
def best_fit(X, Y):
    xbar = sum(X)/len(X)
    ybar = sum(Y)/len(Y)
    n = len(X) # or len(Y)

    numer = sum([xi*yi for xi,yi in zip(X, Y)]) - n * xbar * ybar
    denum = sum([xi**2 for xi in X]) - n * xbar**2

    k = numer / denum
    c = ybar - k * xbar

    return k, c

"""
Run DPS calibration prompts
"""
def do_calibration(comms,args):
    print("For calibration you will need:")
    print("\tA multimeter")
    print("\tA known load capable of handling the required power")
    print("\t2 stable input voltages\r\n")
    print("Please ensure nothing is connected to the output of the DPS before starting calibration!\r\n")

    t = raw_input("Would you like to proceed? (y/n): ")
    if t.lower() != 'y':
        return

    communicate(comms, create_enable_output("off"), args, quiet=True) # Ensure output is off
    start_params = communicate(comms, create_cmd(cmd_query), args, quiet=True) # Fetch current settings so we can restore them after calibrating
    communicate(comms, create_set_function("cv"), args, quiet=True) # Ensure we're in constant voltage mode

    print("Input Voltage Calibration:")
    #Do First voltage hookup, We need the adc values, hopefully
    #peoples computers assign consistent serial ports/IP's
    print("Please hook up the first lower supply voltage to the DPS now")
    print("ensuring that the serial connection is connected after boot")
    v1 = float(raw_input("Type input voltage in mV: "))
    data1 = communicate(comms, create_cmd(cmd_cal_report), args, quiet=True)
    #Do second Voltage Hookup
    print("\r\nPlease hook up the second higher supply voltage to the DPS now")
    print("ensuring that the serial connection is connected after boot")
    v2 = float(raw_input("Type input voltage in mV: "))
    data2 = communicate(comms, create_cmd(cmd_cal_report), args, quiet=True)

    #Math out the calibration constants
    k_adc = (v1-v2)/(data1['vin_adc']-data2['vin_adc'])
    c_adc = v1-k_adc*data1['vin_adc']

    args.calibration_args = ['VIN_ADC_K={}'.format(k_adc),
                            'VIN_ADC_C={}'.format(c_adc)]

    payload = create_set_calibration(args.calibration_args)
    if payload:
        communicate(comms, payload, args, quiet=True)
    print("Input Voltage Calibration Complete\r\n")


    print("Output Voltage Calibration:")
    print("Calibration Point 1 of 2, 10% of Max")
    args.parameter = ["voltage={}".format(v2*.1),"current=1000"]
    payload = create_set_parameter(args.parameter)
    if payload:
        communicate(comms, payload, args, quiet=True)
    communicate(comms, create_enable_output("on"), args, quiet=True)
    c1 = float(raw_input("Type measured voltage on output in mV: "))
    c1_data = communicate(comms, create_cmd(cmd_cal_report), args, quiet=True)

    print("Calibration Point 2 of 2, 90% of Max")
    args.parameter = ["voltage={}".format(v2*.9),"current=1000"]
    payload = create_set_parameter(args.parameter)
    if payload:
        communicate(comms, payload, args, quiet=True)
    c2 = float(raw_input("Type measured voltage on output in mV: "))
    c2_data = communicate(comms, create_cmd(cmd_cal_report), args, quiet=True)
    communicate(comms, create_enable_output("off"), args, quiet=True)
    k_dac = (c1_data['vout_dac']-c2_data['vout_dac'])/(c1-c2)
    c_dac = c1_data['vout_dac']-k_dac*c1
    k_adc = (c1-c2)/(c1_data['vout_adc']-c2_data['vout_adc'])
    c_adc = c1-k_adc*c1_data['vout_adc']

    args.calibration_args = ['V_DAC_K={}'.format(k_dac),
                            'V_DAC_C={}'.format(c_dac),
                            'V_ADC_K={}'.format(k_adc),
                            'V_ADC_C={}'.format(c_adc)]
    payload = create_set_calibration(args.calibration_args)
    if payload:
        communicate(comms, payload, args, quiet=True)
    print("Output Voltage Calibration Complete\r\n")


    print("Output Current Calibration:")
    max_a = float(raw_input("DPS max amperage in mA: "))

    load_resistance = float(raw_input("Load resistance in ohms: "))
    print('Load must be rated for at least {} watts!'.format(round(float((v2/1000)*(v2/1000))/load_resistance),1))
    raw_input("Please connect load to the output of the DPS, then press enter")

    # Take multiple current readings at different voltages and construct an Iout vs Iadc array
    i_out = []
    i_adc = []

    max_v = min(0.9 * v2, max_a * load_resistance) # Calculate max output voltage based on max_output_current * load_resistor
    print("Calibrating", end='')
    for voltage_scaler in range(0, 10):
        output_voltage = max_v * (float(voltage_scaler) / 10)
        args.parameter = ["voltage={}".format(output_voltage),"current={}".format(max_a)]
        i_out.append(output_voltage / load_resistance)
        payload = create_set_parameter(args.parameter)
        if payload:
            communicate(comms, payload, args, quiet=True)
        communicate(comms, create_enable_output("on"), args)
        time.sleep(2) # Wait for DPS output to settle
        data = communicate(comms, create_cmd(cmd_cal_report), args, quiet=True)
        i_adc.append(data['iout_adc'])
        print('.', end='')

    communicate(comms, create_enable_output("off"), args, quiet=True)

    k_adc, c_adc = best_fit(i_adc, i_out) # Calculate gradient and coefficient of line of best fit

    args.calibration_args = ['A_ADC_K={}'.format(k_adc),
                            'A_ADC_C={}'.format(c_adc)]
    payload = create_set_calibration(args.calibration_args)
    if payload:
        communicate(comms, payload, args, quiet=True)
    print("\r\nOutput Current Calibration Complete\r\n") 


    print("Constant Current Calibration:")
    communicate(comms, create_set_function("cc"), args, quiet=True) # Ensure we're in constant current mode

    # Take multiple current readings at different constant currents and construct an Iout vs Idac array
    i_out = []
    i_dac = []

    max_a = min(0.9 * v2 / load_resistance, max_a) # Calculate max output current based on max_output_voltage / load_resistor
    print("Calibrating", end='')
    for current_scaler in range(0, 10):
        output_current = max_a * (float(current_scaler) / 10)
        args.parameter = ["current={}".format(output_current)]
        payload = create_set_parameter(args.parameter)
        if payload:
            communicate(comms, payload, args, quiet=True)
        communicate(comms, create_enable_output("on"), args)
        time.sleep(2) # Wait for DPS output to settle
        data = communicate(comms, create_cmd(cmd_cal_report), args, quiet=True)
        i_dac.append(data['iout_dac'])
        i_out.append(data['iout_adc'] * data['cal']['A_ADC_K'] + data['cal']['A_ADC_C'])
        print('.', end='')

    communicate(comms, create_enable_output("off"), args, quiet=True)

    k_dac, c_dac = best_fit(i_out, i_dac) # Calculate gradient and coefficient of line of best fit

    args.calibration_args = ['A_DAC_K={}'.format(k_dac),
                            'A_DAC_C={}'.format(c_dac)]
    payload = create_set_calibration(args.calibration_args)
    if payload:
        communicate(comms, payload, args, quiet=True)
    print("\r\nConstant Current Calibration Complete\r\n")

    # Restore the original settings
    if start_params['cur_func'] == 'cv':
        communicate(comms, create_set_function("cv"), args, quiet=True)
        args.parameter = ["voltage={}".format(start_params['params']['voltage']),"current={}".format(start_params['params']['current'])]
    if start_params['cur_func'] == 'cc':
        communicate(comms, create_set_function("cc"), args, quiet=True)
        args.parameter = ["current={}".format(start_params['params']['current'])]
    payload = create_set_parameter(args.parameter)
    if payload:
        communicate(comms, payload, args, quiet=True)

    print("Calibration Complete\r\n")
    print("To restore the device to factory defaults use dpsctl.py --init")


"""
Create and return a comminications interface object or None if no comms if
was specified.
"""
def create_comms(args):
    if_name = None
    comms = None
    if args.device:
        if_name = args.device
    elif 'DPSIF' in os.environ and len(os.environ['DPSIF']) > 0:
        if_name = os.environ['DPSIF']

    if if_name != None:
        if is_ip_address(if_name):
            comms = udp_interface(if_name)
        else:
            comms = tty_interface(if_name)
    else:
        fail("no comms interface specified")
    return comms

"""
The worker thread used by uHej for service discovery
"""
def uhej_worker_thread():
    global discovery_list
    global sock
    while 1:
        try:
            data, addr = sock.recvfrom(1024)
            port = addr[1]
            addr = addr[0]
            frame = bytearray(data)
            try:
                f = uhej.decode_frame(frame)
                f["source"] = addr
                f["port"] = port
                types = ["UDP", "TCP", "mcast"]
                if uhej.ANNOUNCE == f["frame_type"]:
                    for s in f["services"]:
                        key = "%s:%s:%s" % (f["source"], s["port"], s["type"])
                        if not key in discovery_list:
                            if s["service_name"] == "opendps":
                                discovery_list[key] = True # Keep track of which hosts we have seen
                                print("%s" % (f["source"]))
#                            print("%16s:%-5d  %-8s %s" % (f["source"], s["port"], types[s["type"]], s["service_name"]))
            except uhej.IllegalFrameException as e:
                pass
        except socket.error as e:
            print('Exception'), e

"""
Scan for OpenDPS devices on the local network
"""
def uhej_scan():
    global discovery_list
    global sock
    discovery_list = {}

    ANY = "0.0.0.0"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    except AttributeError:
        pass
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    sock.bind((ANY, uhej.MCAST_PORT))

    thread = threading.Thread(target = uhej_worker_thread)
    thread.daemon = True
    thread.start()

    sock.setsockopt(socket.SOL_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(uhej.MCAST_GRP) + socket.inet_aton(ANY))

    run_time_s = 6 # Run query for this many seconds
    query_interval_s = 2 # Send query this often
    last_query = 0
    start_time = time.time()

    while time.time() - start_time < run_time_s:
        if time.time() - last_query > query_interval_s:
            f = uhej.query(uhej.UDP, "*")
            sock.sendto(f, (uhej.MCAST_GRP, uhej.MCAST_PORT))
            last_query = time.time()
        time.sleep(1)

    num_found = len(discovery_list)
    if num_found == 0:
        print("No OpenDPS devices found")
    elif num_found == 1:
        print("1 OpenDPS device found")
    else:
        print("%d OpenDPS devices found" % (num_found))


"""
Ye olde main
"""
def main():
    global args
    testing = '--testing' in sys.argv
    parser = argparse.ArgumentParser(description='Instrument an OpenDPS device')

    parser.add_argument('-d', '--device', help="OpenDPS device to connect to. Can be a /dev/tty device or an IP number. If omitted, dpsctl.py will try the environment variable DPSIF", default='')
    parser.add_argument('-S', '--scan', action="store_true", help="Scan for OpenDPS wifi devices")
    parser.add_argument('-f', '--function', nargs='?', help="Set active function")
    parser.add_argument('-F', '--list-functions', action='store_true', help="List available functions")
    parser.add_argument('-p', '--parameter', nargs='+', help="Set function parameter <name>=<value>")
    parser.add_argument('-P', '--list-parameters', action='store_true', help="List function parameters of active function")
    parser.add_argument('-c', '--calibrate', action="store_true", help="Starts System Calibration")
    parser.add_argument('-cr', '--calibration_report', action="store_true", help="Prints Calibration report")
    parser.add_argument('-C', '--calibration_args', nargs='+', help="Set calibration constants <name>=<value>")
    parser.add_argument('-o', '--enable', help="Enable output ('on' or 'off')")
    parser.add_argument(      '--ping', action='store_true', help="Ping device (causes screen to flash)")
    parser.add_argument('-L', '--lock', action='store_true', help="Lock device keys")
    parser.add_argument('-l', '--unlock', action='store_true', help="Unlock device keys")
    parser.add_argument('-q', '--query', action='store_true', help="Query device settings and measurements")
    parser.add_argument('-j', '--json', action='store_true', help="Output parameters as JSON")
    parser.add_argument('-v', '--verbose', action='store_true', help="Verbose communications")
    parser.add_argument('-U', '--upgrade', type=str, dest="firmware", help="Perform upgrade of OpenDPS firmware")
    parser.add_argument('--init', action='store_true', help="Re-inits internal storage")
    parser.add_argument(      '--force', action='store_true', help="Force upgrade even if dpsctl complains about the firmware")
    if testing:
        parser.add_argument('-t', '--temperature', type=str, dest="temperature", help="Send temperature report (for testing)")

    args, unknown = parser.parse_known_args()

    try:
        handle_commands(args)
    except KeyboardInterrupt:
        print("")

if __name__ == "__main__":
    main()
