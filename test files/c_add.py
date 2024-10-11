from threading import Thread
import time
from kubernetes import client, config
from pygnmi.client import gNMIclient
import asyncio
from datetime import datetime

# Configuration parameters
router = {'hostname': 'leaf1', 'ip': '172.20.20.3', 'username': 'admin', 'password': 'NokiaSrl1!'}
r2 = {'hostname': 'router2', 'ip': '192.168.1.2', 'username': 'admin', 'password': 'password'}

ROUTER_COMMAND = "info network-instance ip-vrf-1 protocols bgp-evpn bgp-instance 1"
NAMESPACE = "default"
DEPLOYMENT_NAME = "nginxhello"

CLONE_NEWNET = 0x40000000
SR_CA = '/home/nokia/Documents/Carvalheiro/DCFPartnerHackathon/srl-k8s-anycast-lab/clab-srlk8s/.tls/ca/ca.pem'
SR_USER = 'admin'
SR_PASSWORD = 'NokiaSrl1!'
GNMI_PORT = '57400'
SDK_MGR_FAILED = 'kSdkMgrFailed'
NOS_TYPE = 'SRLinux'
NEIGHBOR_CHASSIS = 'neighbor_chassis'
NEIGHBOR_INT = 'neighbor_int'
LOCAL_INT = 'local_int'
SYS_NAME = 'sys_name'

#replicas = [8, 16, 32, 64, 124]
replicas = [124]
weights = {8:[2, 4, 3], 16:[4, 8, 4], 32:[8, 16, 8], 64:[16, 32, 10], 124: [31, 62, 60]}
times = []
pod_times = []

targets = [
    {"target": ("leaf1", GNMI_PORT), "cert": SR_CA, "user": SR_USER, "password": SR_PASSWORD},
    {"target": ("leaf2", GNMI_PORT), "cert": SR_CA, "user": SR_USER, "password": SR_PASSWORD},
    {"target": ("leaf3", GNMI_PORT), "cert": SR_CA, "user": SR_USER, "password": SR_PASSWORD}
]
path_to_subscribe = '/network-instance[name=ip-vrf-1]/protocols/bgp-evpn/bgp-instance[id=1]/routes/route-table/ip-prefix/evpn-link-bandwidth/advertise/weight'

def deploy_changes(r):
    config.load_kube_config()

    v1 = client.AppsV1Api()

    deployment = v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)

    deployment.spec.replicas = r

    v1.patch_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE, body=deployment)

    return

def reset_pods(r):
    config.load_kube_config()

    v1 = client.AppsV1Api()

    deployment = v1.read_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE)

    deployment.spec.replicas = 4

    v1.patch_namespaced_deployment(name=DEPLOYMENT_NAME, namespace=NAMESPACE, body=deployment)
    time.sleep(r)

def wait_for_pods_to_run(r, result_container):
    config.load_kube_config()
    v1 = client.CoreV1Api()

    while True:
        pods = v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f'app={DEPLOYMENT_NAME}')
        
        running_pods = 0
        for pod in pods.items:
            if pod.status.phase == "Running":
                running_pods += 1
        
        if running_pods == r:
            end_time = datetime.now()
            result_container['time_to_running'] = end_time
            return

def subscribe_to_path(target_info, r):
    changes_timestamps = []
    first_response = True
    change = False
    
    with gNMIclient(target = target_info["target"], username = target_info["user"], password = target_info["password"], path_cert = target_info["cert"], debug = True) as gc:
        subscribe_options = {
            'subscription': [
                {
                    'path': path_to_subscribe,
                    'mode': 'on_change'
                }
            ],
            'use_aliases': False,
            'mode': 'stream'
        }
        c = 0
        # Subscribe and collect changes
        for response in gc.subscribe(subscribe=subscribe_options):
            if first_response:
                if c > 0:
                    first_response = False
                #print(f'FIRST RESPONSE of {target_info["target"]}!!!!!!!!!!!!!!!!!!!!: {response}')
                c += 1
                continue
            for update in response.update.update:
                if(update.val):
                    if target_info["target"] == ("leaf1", GNMI_PORT) or target_info["target"] == ("leaf2", GNMI_PORT):
                        if int(update.val.json_ietf_val) == weights[r][0]:
                            # Only record subsequent changes
                            timestamp = datetime.now()
                            changes_timestamps.append(timestamp)
                            print(f"Change detected on {target_info['target']} at {timestamp}")
                            #print(f'Change response: {response}')
                            change = True
                            break  # Exit the loop after detecting the first change
                    else:
                        if int(update.val.json_ietf_val) == weights[r][1]:
                            # Only record subsequent changes
                            timestamp = datetime.now()
                            changes_timestamps.append(timestamp)
                            print(f"Change detected on {target_info['target']} at {timestamp}")
                            #print(f'Change response: {response}')
                            change = True
                            break  # Exit the loop after detecting the first change
            if change:
                break
    return changes_timestamps

# Main function to run multiple subscriptions asynchronously
async def main(r):
    # Run the synchronous function in separate threads
    loop = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, subscribe_to_path, target_info, r) for target_info in targets]
    
    # Wait for all tasks to complete
    results = await asyncio.gather(*tasks)
    
    # Analyze timestamps to determine when all targets reported changes
    changes = [timestamps for timestamps in results if timestamps]  # Filter out empty results
    if changes:
        # Flatten the list of lists into a single list of timestamps
        all_timestamps = [item for sublist in changes for item in sublist]
        
        # Find the maximum timestamp among the earliest timestamps of each client
        latest_change_time = max(all_timestamps)
        print(f"All clients have reported changes by {latest_change_time}")
        return latest_change_time



if __name__ == "__main__":
    with open('TimetoUpdateCreation1.txt', 'a') as file:
        for r in replicas:
            n = 0
            while n < 20:
                deploy_changes(r)
                initial_time = timestamp = datetime.now()
                print(f'Initial time: {initial_time}')

                 # Create a result container to store thread results
                result_container = {}

                # Start a thread to wait for pods to reach Running state
                pod_thread = Thread(target=wait_for_pods_to_run, args=(r, result_container))
                pod_thread.start()
                
                # Run the network subscription in asyncio
                time_to_converge = asyncio.run(main(r))

                # Wait for the pod thread to finish
                pod_thread.join()

                time_to_running = result_container.get('time_to_running', None)

                print(f'Time to pods running: {time_to_running - initial_time}, Time to network converge: {time_to_converge - initial_time}. Iteration {n}:{r}')
                time.sleep(0.5)
                reset_pods(r)
                times.append((time_to_converge - initial_time).total_seconds())
                pod_times.append((time_to_running - initial_time).total_seconds())
                time.sleep(3)
                n += 1
            print("\n\n------------------------------------------------------------", file=file)
            print(f"\n\nAdding {r} pods", file=file)
            print(f"Total time: {times}", file=file)
            print(f"Pod time: {pod_times}", file=file)
            print(f"Network converge time: {[total - pod for total, pod in zip(times, pod_times)]}", file=file)
            print(times)
            times.clear()
            pod_times.clear()