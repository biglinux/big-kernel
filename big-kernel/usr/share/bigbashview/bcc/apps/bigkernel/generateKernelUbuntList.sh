#!/bin/bash

OLDIFS=$IFS
IFS=$'\n'

wget http://kernel.ubuntu.com/~kernel-ppa/mainline/ -O /tmp/ubuntuKernel.txt

grep -e 'href="v4' -e 'href="v5' /tmp/ubuntuKernel.txt | sed 's|.*href="||g;s|/".*||g' | tac > /tmp/ubuntuKernelFiltered.txt

IFS=$OLDIFS
