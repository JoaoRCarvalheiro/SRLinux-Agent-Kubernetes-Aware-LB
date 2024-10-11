#!/usr/bin/env python
# coding=utf-8

import traceback
import grpc
import datetime
import sys
import logging
import socket
import os
import ctypes
import signal
import time
from kubernetes import client, config
from logging.handlers import RotatingFileHandler
from pygnmi.client import gNMIclient
from ndk.sdk_service_pb2_grpc import SdkMgrServiceStub
from ndk.sdk_service_pb2_grpc import SdkNotificationServiceStub
from ndk.sdk_service_pb2 import AgentRegistrationRequest

CLONE_NEWNET = 0x40000000
SR_CA = '/ca.pem'
SR_USER = 'admin'
SR_PASSWORD = 'NokiaSrl1!'
GNMI_PORT = '57400'
SDK_MGR_FAILED = 'kSdkMgrFailed'
NOS_TYPE = 'SRLinux'
NEIGHBOR_CHASSIS = 'neighbor_chassis'
NEIGHBOR_INT = 'neighbor_int'
LOCAL_INT = 'local_int'
SYS_NAME = 'sys_name'

agent_name ='srkubeloadbalance'
channel = grpc.insecure_channel('localhost:50053')
metadata = [('agent_name', agent_name)]
stub = SdkMgrServiceStub(channel)
sub_stub = SdkNotificationServiceStub(channel)

leafname = ''

l_pods = {}
l_aux_p = {}
active_static_routes = {}
l_externalIP = {}
l_neighbors = [{'host_ip': '192.168.49.2', 'int_ip': '192.168.1.11', 'hostname': 'leaf1'}, {'host_ip': '192.168.49.3', 'int_ip': '192.168.1.12', 'hostname': 'leaf2'}, 
               {'host_ip': '192.168.49.4', 'int_ip': '192.168.1.13', 'hostname': 'leaf3'}, {'host_ip': '192.168.49.5', 'int_ip': '192.168.1.14', 'hostname': 'leaf3'}]

################FUNCTIONS TO INITIALIZE NOTIFICATIONS#####################
def getPods():
    try:
        config.load_kube_config(config_file='/home/nokia/.kube/config')

        v1 = client.CoreV1Api()
        
        res_pods = v1.list_namespaced_pod('default')
        
        pods = {}
        aux_p = {}
        routes = {}
        externalIP = {}
        leaf1_host_ips = {neighbor['host_ip'] for neighbor in l_neighbors if neighbor['hostname'] == leafname}

        global l_pods
        global active_static_routes
        global l_aux_p
        global l_externalIP

        for item in res_pods.items:
            if item.status.host_ip in leaf1_host_ips and item.status.phase == 'Running':
                p = {'pod_ip': item.status.pod_ip, 'host_ip': item.status.host_ip, 'namespace': item.metadata.namespace,
                    'name': item.metadata.name, 'phase': item.status.phase}
                service = item.metadata.name.split('-')[0]

                if service in pods:
                    pods[service].append(p)
                    aux_p[service].append(item.metadata.name + ":" + item.status.phase + ":" + item.status.pod_ip)
                else:
                    pods[service] = [p]
                    aux_p[service] = [item.metadata.name + ":" + item.status.phase + ":" + item.status.pod_ip]

                #Get active routes
                if service not in routes:
                    routes[service] = [item.status.host_ip]
                else:
                    if item.status.host_ip not in routes[service]:
                        routes[service].append(item.status.host_ip)
        logging.info(f'ROUTES: {routes}\n')
        for s in pods.keys():
            res_services = v1.read_namespaced_service(name=s, namespace='default')
            external_ip = res_services.status.load_balancer.ingress
            logging.info(f'External IP: {externalIP} \n')
            if external_ip:
                for ingress in external_ip:
                    externalIP[s] = ingress.ip
            #logging.info(f'External IP: {externalIP} \n')
        
        #if len(l_pods) > 0:
        if l_pods != pods:
            #logging.info('ENTROU PARA DAR UPDATE!!!!!!!!!!!!!!!!!!!!! \n')
            updatePodStructure(pods, aux_p, routes, externalIP)

        l_pods = pods
        l_aux_p = aux_p
        active_static_routes = routes
        l_externalIP = externalIP
        logging.info(f'Final list of pods: {pods}\n')
        return None
    except Exception as e:
        logging.error(f"Error in getPods: {e}")
        logging.error(traceback.format_exc())

def updatePodStructure(pods:dict, aux_p:dict, routes:dict, externalIP:dict) -> None:
    try:
        services = set(l_pods.keys()).union(set(pods.keys()))
        update_ecmp = {}
        logging.info(f"SERVICES: {services}")
        for service in services:
            if service in aux_p:
                new_set = set(aux_p[service])
            else: 
                new_set = set()
            if service in l_aux_p:
                old_set = set(l_aux_p[service])
            else:
                old_set = set()
            
            if service in active_static_routes:
                old_routes_set = set(active_static_routes[service])
            else:
                old_routes_set = set()
            
            if service in routes:
                new_r = set(routes[service])
            else:
                new_r = set()

            deleted_routes = {}
            new_routes = {}

            # Find new elements
            new_elements = new_set - old_set
            # Find deleted elements
            deleted_elements = old_set - new_set

            logging.info(f"NEW ELEMENTS: {new_elements}")
            logging.info(f"OLD ELEMENTS: {deleted_elements}")

            if not sorted(old_routes_set) == sorted(new_r):
                deleted_routes = old_routes_set - new_r
                new_routes = new_r - old_routes_set
            logging.info(f"NEW ROUTES {new_routes}")
            logging.info(f"DELETED ROUTES {deleted_routes}")

            for e in new_elements:
                s = e.split(":")
                for pod in pods[service]:
                    if pod["phase"] == 'Running' and pod["name"] == s[0]:
                        logging.info(f"ELEMENT TO BE ADDED: {pod}")
                        if len(new_routes) > 0:
                            if pod["host_ip"] in new_routes:
                                updateNextHops(pod["host_ip"], pod['host_ip'].split('.')[3], service, True, routes)
                                if service not in l_pods:
                                    updateStaticRoutes(externalIP[service], service, True)
                                new_routes.remove(pod["host_ip"])
                                logging.info("ADDING ROTE TO NODE: " + str(pod["host_ip"]))
                        #ATUALIZAR ECMP
                        #logging.info("PASSOU FAZER UPDATE NOVAS!!!!!!!!!!! " + str(s[0]))
                        if pod["host_ip"] not in update_ecmp:
                            update_ecmp[pod["host_ip"]] = 1
                            #logging.info(f"FICOU IGUAL 1 A: {update_ecmp}")
                        else:
                            update_ecmp[pod["host_ip"]] += 1
                            #logging.info(f"ADICIONOU 1 A: {update_ecmp}")
                        break

            for e in deleted_elements:
                s = e.split(":")
                for pod in l_pods[service]:
                    if pod['name'] == s[0]:
                        if len(deleted_routes) > 0: #APAGAR ROTA
                            if service not in pods: #VER SE AINDA EXISTEM PODS A USAR ESSA ROTA ESTATICA
                                updateStaticRoutes(l_externalIP[service], service, False)
                                logging.info("DELETE ROTE TO POD: " + str(pod))
                                updateNextHops(pod["host_ip"], pod['host_ip'].split('.')[3], service, False, routes)
                            else: #TODO: QUANDO SE APAGA SO UM DOS NEXT-HOPS NAO APAGA NENHUM E SEM ESTE FOR APAGA OS DOIS
                                i = 0
                                for p in pods[service]:
                                    if p["host_ip"] == pod["host_ip"]:
                                        i += 1
                                if i == 0:
                                    updateNextHops(pod["host_ip"], pod['host_ip'].split('.')[3], service, False, routes)
                        if pod["host_ip"] not in update_ecmp:
                            update_ecmp[pod["host_ip"]] = -1
                        else:
                            update_ecmp[pod["host_ip"]] -= 1
                        break

        logging.info(f"UPDATE ECMP: {update_ecmp}")
        updateEcmp(update_ecmp)

    except Exception as e:
        logging.error(f"Error in updatePodStructure: {e}")
        logging.error(traceback.format_exc())

def updateEcmp(update_ecmp): #Updates of ecmp weights
    gnmic_host = (leafname, GNMI_PORT)
    with gNMIclient(target=gnmic_host, path_cert=SR_CA, username=SR_USER, password=SR_PASSWORD, debug=True) as gc:
        w = 0
        for key in update_ecmp: #Loop with the changes that need to be done
            w += update_ecmp[key]

        if len(l_pods) > 0:
            initialWeight = 0
            result = gc.get(path=["/network-instance[name=ip-vrf-1]/protocols/bgp-evpn/bgp-instance[id=1]"], encoding="json_ietf") #Get's the current ecmp weight
            if 'update' in result['notification'][0]:
                if 'advertise' in result['notification'][0]['update'][0]['val']['routes']['route-table']['ip-prefix']['evpn-link-bandwidth']:
                    initialWeight += int(result['notification'][0]['update'][0]['val']['routes']['route-table']['ip-prefix']['evpn-link-bandwidth']['advertise']['weight'])
            
            w += initialWeight
        logging.info('WEIGHT TO PUT: ' + str(w) + 'IN: ' + str(key))
            
        if w <= 0: #TODO: So se for o ultimo e que muda
            weight = {'advertise': {'weight':1}}
        else:
            if w > 128:
                w = 128
            weight = {'advertise': {'weight':str(w)}}
        
        update = [('/network-instance[name=ip-vrf-1]/protocols/bgp-evpn/bgp-instance[id=1]/routes/route-table/ip-prefix/evpn-link-bandwidth/', weight)]
        result = gc.set(update=update, encoding="json_ietf")
        #logging.info(f"Result of update srlinux: {result}")

def updateStaticRoutes(externalIP, service, newRoute):
    gnmic_host = (leafname, GNMI_PORT)
    with gNMIclient(target=gnmic_host, path_cert=SR_CA, username=SR_USER, password=SR_PASSWORD, debug=True) as gc:
        if newRoute:
            static_route = {'admin-state' : 'enable',
                            'next-hop-group' : service
                        }
            update = [(f'/network-instance[name=ip-vrf-1]/static-routes/route[prefix={externalIP}/32]/', static_route)]
            gc.set(update=update, encoding="json_ietf")
            logging.info(f"STATIC-ROUTE CREATED: {externalIP}:{service}")
        else:
            gc.set(delete=[f"/network-instance[name=ip-vrf-1]/static-routes/route[prefix={externalIP}/32]"], encoding="json_ietf")
            logging.info(f"STATIC-ROUTE DELETED: {externalIP}:{service}")

def updateNextHops(newIp, index_number, service, newRoute, routes):
    gnmic_host = (leafname, GNMI_PORT)
    with gNMIclient(target=gnmic_host, path_cert=SR_CA, username=SR_USER, password=SR_PASSWORD, debug=True) as gc:
        if newRoute:
            interfaceIp = ""
            for neighbor in l_neighbors:
                if neighbor['host_ip'] == newIp:
                    interfaceIp = neighbor['int_ip']
            next_hop = {'admin-state' : 'enable',
                        'ip-address' : str(interfaceIp)
                        }
            update = [(f'/network-instance[name=ip-vrf-1]/next-hop-groups/group[name={service}]/nexthop[index={index_number}]/', next_hop)]
            gc.set(update=update, encoding="json_ietf")
            logging.info(f"NEXT-HOP ADDED: {newIp}:{service}")

        else:
            if service in routes:
                gc.set(delete=[f"/network-instance[name=ip-vrf-1]/next-hop-groups/group[name={service}]/nexthop[index={index_number}]"], encoding="json_ietf")
            else:
                gc.set(delete=[f"/network-instance[name=ip-vrf-1]/next-hop-groups/group[name={service}]"], encoding="json_ietf")
            logging.info('NEXT-HOP DELETED: ' + str(newIp))

def Run():
    gnmic_host = (leafname, GNMI_PORT)
    with gNMIclient(target=gnmic_host, path_cert=SR_CA, username=SR_USER, password=SR_PASSWORD, debug=True) as gc: 
        weight = {'weighted-ecmp': {'admin-state':'enable'}}
        update = [('/network-instance[name=ip-vrf-1]/protocols/bgp-evpn/bgp-instance[id=1]/routes/route-table/ip-prefix/evpn-link-bandwidth/', weight)]
        gc.set(update=update, encoding="json_ietf")

    try:
        count = 0
        while True:
            logging.info(f"Getting kubernetes information :: {count}\n")
            getPods()
            count += 1
            time.sleep(1)
    except Exception as e:
            logging.error('Exception caught :: {}'.format(str(e)))

def Exit_Gracefully(signum, frame):
    logging.info("Caught signal :: {}\n will unregister fib_agent".format(signum))
    try:
        response=stub.AgentUnRegister(request=AgentRegistrationRequest(), metadata=metadata)
        logging.error('try: Unregister response:: {}'.format(response))
        sys.exit()
    except grpc._channel._Rendezvous as err:
        logging.info('GOING TO EXIT NOW: {}'.format(err))
        sys.exit()

if __name__ == '__main__':
    stdout_dir = '/var/log/srlinux/stdout'
    signal.signal(signal.SIGTERM, Exit_Gracefully)
    hostname = socket.gethostname()
    if not os.path.exists(stdout_dir):
        os.makedirs(stdout_dir, exist_ok=True)
    log_filename = '{}/{}_srkube.log'.format(stdout_dir, hostname)
    logging.basicConfig(filename=log_filename, filemode='a',\
                        format='[%(levelname)s %(asctime)s,%(msecs)d %(name)s]',\
                        datefmt='%H:%M:%S', level=logging.INFO)
    handler = RotatingFileHandler(log_filename, maxBytes=3000000,
                                  backupCount=5)
    logging.getLogger().addHandler(handler)

    ns_path = '/var/run/netns/srbase-mgmt'
    ns_fd = os.open(ns_path, os.O_RDONLY)
    logging.info(f"ns_fd: {ns_fd}")
    print(f"ns_fd: {ns_fd}")
    libc = ctypes.CDLL('libc.so.6')
    setns = libc.setns
    setns.argtypes = [ctypes.c_int, ctypes.c_int]
    if setns(ns_fd, CLONE_NEWNET) == -1:
        raise Exception("Failed to set network namespace")

    leafname = hostname
    logging.info("START TIME :: {}".format(datetime.datetime.now()))
    if Run():
        logging.info('Agent unregistered and agent routes withdrawed from dut')
    else:
        logging.info(f'Some exception caught, Check !')