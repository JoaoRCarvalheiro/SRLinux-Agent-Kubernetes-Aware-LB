# Nokia SR Linux agent to implement Kubernetes aware load balancing in data centers

In this lab we will explore a topology consisting of a Leaf/Spine [SR Linux](https://learn.srlinux.dev/) Fabric connected to a Kubernetes Cluster.

Our k8s Cluster will feature [MetalLB](https://metallb.universe.tf/), which is a load-balancer implementation for bare metal clusters. This will unlock the possibility to have **anycast** services in the SR Linux fabric.

To deploy this lab we will use [Containerlab](https://containerlab.dev/) which help us to effortlessly create complex network topologies and validate features, scenarios... And also, [Minikube](https://minikube.sigs.k8s.io/) which is an open-source tool that facilitates running Kubernetes clusters locally to quickly test and experiment with containerized applications.

The end service we will use on top of the kubernetes cluster is a Nginx HTTP echo server. This service will be deployed and exposed in all the k8s nodes. With simulated clients, we will verify how traffic is distributed among the different nodes/pods.

This lab. is using metallb in L2 Mode. Static routes are programmed in each ToR switch targeting the Metallb service IPs. The Next-Hops of the static routes are the worker node interfaces.

The following diagram represent the physical an logical topology.

## Topology

<p align="center">
 <img src="images/LabTop.png" width="900">
</p>

## Goal

Demonstrate kubernetes MetalLB load balancing in L2 Mode using a Containerlab+Minikube Lab.

## Features

- Containerlab topology
- Minikube kubernetes cluster (4 nodes)
- MetalLB integration
- Preconfigured Leaf/Spine Fabric: 2xSpine, 4xLeaf SR Linux switches
- Anycast services
- Linux clients to simulate connections to k8s services (4 clients)

## Requirements

- [Containerlab](https://containerlab.dev/)
- [minikube](https://minikube.sigs.k8s.io)
- [Docker](https://docs.docker.com/engine/install/)
- [SR Linux Container image](https://github.com/nokia/srlinux-container-image)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)

## Deploying the lab

```bash
cd DCFPartnerHackathon/srl-k8s-anycast-lab
```

```bash
# deploy minikube cluster
minikube start --nodes 4 -p cluster1
```

```bash
# deploy containerlab topology
clab deploy --topo srl-k8s-lab.clab.yml
```

```bash
# enable MetalLB addons
minikube addons enable metallb -p cluster1
```

```bash
# install MetalLB (native mode l2advertisement)
kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.13.12/config/manifests/metallb-native.yaml
```

```bash
# setup MetalLB
kubectl apply -f metallb.yaml
```

```bash
# Add k8s HTTP echo deployment and LB service
kubectl apply -f nginx.yaml
```

## Tests

```bash
# check underlay sessions in Spine, leaf switches
A:spine1$ show network-instance default protocols bgp neighbor

# check the static route is up on all the leaf switches
A:leaf2$ show network-instance ip-vrf-1 protocols bgp neighbor

# check kubernetes status
kubectl get nodes -o wide

kubectl get pods -o wide

kubectl get svc

# check MetalLB BGP speaker pods in kubernetes nodes
kubectl get pods -A | grep speaker

# connect to MetalLB speaker pod
# change speaker-4gcj8 with the name of one of the speakers
kubectl exec -it speaker-4gcj8 --namespace=metallb-system -- vtysh

# verify BGP sessions in FRR daemon
cluster1$ show bgp summary

# verify running config of FRR daemon
cluster1$ show run

# check HTTP echo service
docker exec -it client4 curl 2.2.2.100
Server address: 10.244.0.3:80
Server name: nginxhello-6b97fd8857-4vp6z
Date: 10/Aug/2023:09:06:01 +0000
URI: /
Request ID: f84edead22027f72b2dc951fbfe96b4f

# check HTTP echo service once again
docker exec -it client4 curl 1.1.1.100
Server address: 10.244.2.3:80
Server name: nginxhello-6b97fd8857-b2vf8
Date: 10/Aug/2023:09:06:03 +0000
URI: /
Request ID: f03d053c39bd725519e86fa5b588f7f6

# requests are load balanced to different pods
```

## Delete the lab

```bash
# destroy clab topology and cleanup 
clab destroy --topo srl-k8s-lab.clab.yml --cleanup
```

```bash
# delete Minikube cluster
minikube delete --all
```
