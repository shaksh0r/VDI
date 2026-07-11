#!/bin/sh

# Host gateway (your app_net bridge)
GW=10.200.3.1

# Add all VPN subnets that need to be reachable
ip route add 10.10.0.0/24  via $GW 2>/dev/null || true
ip route add 10.20.0.0/24  via $GW 2>/dev/null || true
ip route add 10.100.0.0/24 via $GW 2>/dev/null || true
ip route add 10.200.0.0/24 via $GW 2>/dev/null || true
ip route add 172.17.0.0/24 via $GW 2>/dev/null || true
ip route add 172.18.0.0/24 via $GW 2>/dev/null || true
ip route add 172.19.0.0/24 via $GW 2>/dev/null || true
ip route add 172.20.0.0/24 via $GW 2>/dev/null || true
ip route add 172.27.0.0/24 via $GW 2>/dev/null || true
ip route add 172.28.0.0/24 via $GW 2>/dev/null || true
ip route add 172.29.0.0/24 via $GW 2>/dev/null || true
ip route add 172.30.0.0/24 via $GW 2>/dev/null || true
ip route add 192.168.19.0/24 via $GW 2>/dev/null || true
ip route add 192.168.64.0/21 via $GW 2>/dev/null || true
ip route add 192.168.92.0/24 via $GW 2>/dev/null || true

echo "VPN routes added"