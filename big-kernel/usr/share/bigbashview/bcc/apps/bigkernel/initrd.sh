#!/bin/bash

xtermset -geom 100x15+30+110

update-initramfs -c -k all
update-grub
