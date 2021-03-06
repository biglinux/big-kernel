#!/bin/bash

xtermset -geom 100x15+30+110

OLDIFS=$IFS
IFS=$'\n'


rm -Rf /tmp/installKernelUbuntu
mkdir /tmp/installKernelUbuntu 2> /dev/null
cd /tmp/installKernelUbuntu

for x  in  $(grep " install$" /tmp/bigKernelGuiInstall.txt | sed 's| install||g'); do

if [ "$(curl https://kernel.ubuntu.com/~kernel-ppa/mainline/$(echo "$x" | sed 's|-generic||g;s|-lowlatency||g')/ | grep -i "Build for amd64 failed")" != "" ]; then

    kdialog --msgbox $"Esta versão de kernel não está disponível para download, tente outra versão." --title "Kernel"

else

  if [ "$(echo "$x" | grep generic)" != "" ]; then
    for i  in  $(curl https://kernel.ubuntu.com/~kernel-ppa/mainline/$(echo "$x" | sed 's|-generic||g;s|-lowlatency||g')/ | grep "amd64\|all" | sed 's|.*href="||g;s|">.*||g' | grep "_all\|-generic" | sort | uniq); do
        wget "http://kernel.ubuntu.com/~kernel-ppa/mainline/$(echo "$x" | sed 's|-generic||g;s|-lowlatency||g')/$i"
    done
  else
    for i  in  $(curl https://kernel.ubuntu.com/~kernel-ppa/mainline/$(echo "$x" | sed 's|-generic||g;s|-lowlatency||g')/ | grep "amd64\|all" | sed 's|.*href="||g;s|">.*||g' | grep "_all\|-lowlatency" | sort | uniq); do
        wget "http://kernel.ubuntu.com/~kernel-ppa/mainline/$(echo "$x" | sed 's|-generic||g;s|-lowlatency||g')/$i"
    done
  fi

fi

done

IFS=$OLDIFS

dpkg -i /tmp/installKernelUbuntu/*

rm -Rf /tmp/installKernelUbuntu/

image="$(grep " uninstall$" /tmp/bigKernelGuiInstall.txt | sed 's| uninstall||g' | sed ':a;N;$!ba;s/\n/ /g')"
header="$(grep " uninstall$" /tmp/bigKernelGuiInstall.txt | sed 's| uninstall||g' | sed ':a;N;$!ba;s/\n/ /g' | sed 's|image|headers|g;s|-generic||g;s|-lowlatency||g')"
header2="$(grep " uninstall$" /tmp/bigKernelGuiInstall.txt | sed 's| uninstall||g' | sed ':a;N;$!ba;s/\n/ /g' | sed 's|image|headers|g')"

dpkg -P  $image
dpkg -P $header2
dpkg -P $header
