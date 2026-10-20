version: 2
ethernets:
  generic0:
    match:
      macaddress: "${mac}"
    set-name: generic0
    dhcp4: false
    dhcp6: false
    addresses:
      - ${address}/${prefix}
    routes:
      - to: default
        via: ${gateway}
    nameservers:
      addresses:
        - ${dns}
