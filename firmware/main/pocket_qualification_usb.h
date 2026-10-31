/* Qualification build only. No hardware operation occurs unless called on target. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
/* Boot only, before application tasks. Never uninstalls or appropriates a driver. */
bool pocket_qualification_usb_init(void);
bool pocket_qualification_usb_available(void);
/* One serialized owner. Begin suppresses ordinary formatted logs, not panic/ROM
 * writes; those invalidate framed data. End only after audio cleanup. */
bool pocket_qualification_usb_export_begin(void);
int pocket_qualification_usb_export_ready(void); /* -1 failed,0 drain pending,1 ready */
void pocket_qualification_usb_export_end(void);
int pocket_qualification_usb_write(const uint8_t*,size_t); /* -1 failure,0 busy,1 whole record queued, NOT host receipt */
int pocket_qualification_usb_read(uint8_t*,size_t); /* nonblocking, <=256 bytes */
