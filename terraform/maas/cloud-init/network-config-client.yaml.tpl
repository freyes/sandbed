version: 2
ethernets:
  mgmt0:
    match:
      macaddress: "${mgmt_mac}"
    set-name: mgmt0
    dhcp4: true
  generic0:
    match:
      macaddress: "${generic_mac}"
    set-name: generic0
    dhcp4: false
    dhcp6: false
    addresses:
      - ${address}/${prefix}
