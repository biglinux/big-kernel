#!/bin/bash

OLDIFS=$IFS
IFS=$'\n'

all="$(apt-cache search linux-image | grep -ve 686 -ve 586 -ve dbg -ve nvidia -ve linux-headers -ve meta-package | cut -f1 -d " " | sort -Vr)"
installed="$(dpkg-query -l '*linux-image-[0-9]*' | grep ^ii  | awk -F ' ' '{print $2}')"

echo "$all" > /tmp/bigKernelAll
echo "$installed" > /tmp/bigKernelInstalled

# filter installed
for i  in $(cat /tmp/bigKernelInstalled); do
  for x  in  $(cat /tmp/bigKernelAll); do
      if [ "$i" = "$x" ]; then
          sed -i "s|$x|$x installed|g" /tmp/bigKernelAll
      fi
  done
done

# make Kernel Deepin list
grep deepin /tmp/bigKernelAll > /tmp/bigKernelDeepin


# Kernel without deepin or ubuntu
cp -f /tmp/bigKernelAll /tmp/bigKernelWithoutDeepin
for y in $(cat /tmp/bigKernelDeepin); do
    sed -i "s|^$y$||g" /tmp/bigKernelWithoutDeepin
    sed -i '/^$/d' /tmp/bigKernelWithoutDeepin
done
grep -v "generic" /tmp/bigKernelWithoutDeepin | grep -v lowlatency > /tmp/bigKernelWithoutDeepin2
rm -f /tmp/bigKernelWithoutDeepin
mv -f /tmp/bigKernelWithoutDeepin2 /tmp/bigKernelWithoutDeepin

IFS=$OLDIFS
