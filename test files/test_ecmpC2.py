from scapy.all import *
import random
import time
import re
import pandas

destination_ip = "2.2.2.100"
destination_port = 80

spoofed_ip = ["192.168.2.30", "192.168.2.31", "192.168.2.32", "192.168.2.33", "192.168.2.34", "192.168.2.35", "192.168.2.36", 
              "192.168.2.37", "192.168.2.38", "192.168.2.39", "192.168.2.40", "192.168.2.41", "192.168.2.42", "192.168.2.43", "192.168.2.44"]
arp_target_ip = "192.168.2.1"
interface = "eth0"

results_list = {}
results_list_lock = Lock()

http_request = (
    "GET / HTTP/1.1\r\n"
    "Host: 2.2.2.100\r\n"
    "User-Agent: scapy\r\n"
    "Accept: */*\r\n"
    "\r\n"
)

class TcpSession:
    def __init__(self,target):
        self.seq = 0
        self.ack = 0
        self.srcIp = target[0]
        self.ip = IP(src=target[0], dst=target[1])
        self.sport = random.randint(1024, 65535)
        self.dport = 80
        self.connected = False
        self._ackThread = None
        self._timeout = 3
      
    def _ack(self, packet):
        self.ack = packet[TCP].seq + len(packet[Raw])
        ack = self.ip/TCP(sport=self.sport, dport=self.dport, flags='A', seq=self.seq, ack=self.ack)
        send(ack)
    
    def _sniff(self):
        s = L3RawSocket()
        while self.connected:
            http_response = b""
            p = s.recv(MTU)
            if p.haslayer(TCP) and p.haslayer(Raw) and p[TCP].dport == self.sport and p[IP].dst == self.srcIp:
                    self._ack(p)
                    if p[0].haslayer(Raw):
                        http_response += p[0][Raw].load
                        match = re.search(r'Server address: (\d+\.\d+\.\d+\.\d+):\d+', http_response.decode('utf-8'))
                        if match:
                            with results_list_lock:
                                if results_list.get(self.srcIp):
                                    results_list[self.srcIp].append(match.group(1))
                                else:
                                    results_list[self.srcIp] = [match.group(1)]
                
        s.close()
        self._ackThread = None
        print('Acknowledgment thread stopped')

    def _start_ackThread(self):
        self._ackThread = Thread(name='AckThread',target=self._sniff)
        self._ackThread.start()

    def connect(self):
        self.seq = random.randrange(0,(2**32)-1)

        syn = self.ip/TCP(sport=self.sport, dport=self.dport, seq=self.seq, flags='S')
        syn_ack = sr1(syn, timeout=self._timeout)
        self.seq += 1
        
        assert syn_ack.haslayer(TCP) , 'TCP layer missing'
        assert syn_ack[TCP].flags & 0x12 == 0x12 , 'No SYN/ACK flags'
        assert syn_ack[TCP].ack == self.seq , 'Acknowledgment number error'

        self.ack = syn_ack[TCP].seq + 1
        ack = self.ip/TCP(sport=self.sport, dport=self.dport, seq=self.seq, flags='A', ack=self.ack)
        send(ack)

        self.connected = True
        self._start_ackThread()
        print('Connected')

    def close(self):
        self.connected = False

        fin = self.ip/TCP(sport=self.sport, dport=self.dport, flags='FA', seq=self.seq, ack=self.ack)
        fin_ack = sr1(fin, timeout=self._timeout)
        self.seq += 1

        assert fin_ack.haslayer(TCP), 'TCP layer missing'
        assert fin_ack[TCP].flags & 0x11 == 0x11 , 'No FIN/ACK flags'
        assert fin_ack[TCP].ack == self.seq , 'Acknowledgment number error'

        self.ack = fin_ack[TCP].seq + 1
        ack = self.ip/TCP(sport=self.sport, dport=self.dport, flags='A', seq=self.seq,  ack=self.ack)
        send(ack)

        print('Disconnected')

    def send(self, payload):
        psh = self.ip/TCP(sport=self.sport, dport=self.dport, flags='PA', seq=self.seq, ack=self.ack)/payload
        self.seq += len(psh[Raw])
        send(psh)
        time.sleep(random.uniform(0.1, 0.3))


def update_arp_table(spoofed_ip, target_ip, interface):
    # Find the MAC address of the interface
    int_mac = get_if_hwaddr(interface)
    #ARP reply packet
    arp_reply = ARP(
        op=2,  # ARP reply
        psrc=spoofed_ip,
        pdst=target_ip,
        hwdst="ff:ff:ff:ff:ff:ff",  # Broadcast
        hwsrc=int_mac
    )

    send(arp_reply, iface=interface)

def send_packets(s):
    count = 0
    while count < 3:
        s.connect()
        s.send(http_request)
        s.close()
        count += 1

def structure_output():
    rows = []
    all_servers = set()
    for src_ip, servers in results_list.items():
        row = {'Source IP': src_ip}
        for server in servers:
            if server in row:
                row[server] += 1
            else:
                row[server] = 1
            all_servers.add(server)
        rows.append(row)
        
    df = pandas.DataFrame(rows)
    df = df.fillna(0)
    df.set_index('Source IP', inplace=True)

    for server in all_servers:
        if server not in df.columns:
            df[server] = 0

    total = df.sum(axis=0)
    total.name = 'Total'

    # Append the total row to the DataFrame
    df = pandas.concat([df, total.to_frame().T])

    # Print the DataFrame as a table
    print(df)
    with open('results.txt', 'a') as f:
        print("\n\n------------------------------------------------------------", file=f)
        print(df, file=f)


if __name__ == '__main__':
    
    count = 0
    c = 0
    prefix = 45
    arp = False
    while count < 10:
        while c < 30:
            tcp = []
            threads = []
            while prefix < 75:
                src = '192.168.2.' + str(prefix)
                if not arp:
                    update_arp_table(src, arp_target_ip, interface)
                s = TcpSession((src, destination_ip))
                tcp.append(s)
                prefix += 1
            arp = True
            time.sleep(0.5)
            for s in tcp:
                t = Thread(target=send_packets, args=(s,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join()
            c +=1
            prefix = 45
        c = 0
        count += 1
        print(results_list)
        structure_output()
        results_list.clear()
