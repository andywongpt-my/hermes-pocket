/* MIT. Qualification-only USB owner. Queue admission is not host receipt.
 * Driver/VFS installation is boot-only and persists for this entire boot. */
#include "pocket_qualification_usb.h"
#include "sdkconfig.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_log.h"
#include <stdatomic.h>
static bool attempted;
static atomic_bool available;
static atomic_bool exporting;
static atomic_uint forwarding_logs;
static vprintf_like_t previous_log;
static int guarded_log(const char *format,va_list args){
 /* Count BEFORE the quiet check. Sequential consistency orders a logger that
  * observed non-quiet before export_begin against export_ready's drain barrier.
  * The latter must not pass while an earlier formatter can still enqueue text. */
 atomic_fetch_add_explicit(&forwarding_logs,1,memory_order_seq_cst);
 int result=0;
 if(!atomic_load_explicit(&exporting,memory_order_seq_cst) && previous_log)
  result=previous_log(format,args);
 atomic_fetch_sub_explicit(&forwarding_logs,1,memory_order_seq_cst);
 return result;
}
bool pocket_qualification_usb_available(void){return atomic_load_explicit(&available,memory_order_acquire);}
bool pocket_qualification_usb_init(void){
 /* Called once from app_main before normal application tasks. Never borrow or
  * uninstall an unknown driver, even if it appears compatible. */
 if(attempted)return pocket_qualification_usb_available();
 attempted=true;
#if !CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG || !CONFIG_ESP_CONSOLE_SECONDARY_NONE
 return false;
#else
 if(usb_serial_jtag_is_driver_installed())return false;
 usb_serial_jtag_driver_config_t config={.tx_buffer_size=4096,.rx_buffer_size=4096};
 if(usb_serial_jtag_driver_install(&config)!=ESP_OK)return false;
 usb_serial_jtag_vfs_use_driver();
 previous_log=esp_log_set_vprintf(guarded_log);
 atomic_store_explicit(&available,true,memory_order_release);return true;
#endif
}
bool pocket_qualification_usb_export_begin(void){
 if(!pocket_qualification_usb_available())return false;
 bool expected=false;
 return atomic_compare_exchange_strong_explicit(&exporting,&expected,true,memory_order_seq_cst,memory_order_seq_cst);
}
int pocket_qualification_usb_export_ready(void){
 if(!pocket_qualification_usb_available()||!atomic_load_explicit(&exporting,memory_order_acquire))return -1;
 /* Zero driver timeout. Owner supplies the finite outer wait/Stop checks. A
  * caller must wait for this before START; previous log bytes are only a prefix. */
 if(atomic_load_explicit(&forwarding_logs,memory_order_seq_cst))return 0;
 esp_err_t error=usb_serial_jtag_wait_tx_done(0);
 if(error==ESP_OK)return 1;
 if(error==ESP_ERR_TIMEOUT)return 0;
 atomic_store_explicit(&available,false,memory_order_release);return -1;
}
void pocket_qualification_usb_export_end(void){
 /* No uninstall, VFS switch or log-hook replacement beneath running tasks. */
 atomic_store_explicit(&exporting,false,memory_order_seq_cst);
}
int pocket_qualification_usb_write(const uint8_t *record,size_t bytes){
 if(!record||bytes<32||bytes>1056||!pocket_qualification_usb_available()||
    !atomic_load_explicit(&exporting,memory_order_acquire))return -1;
 /* Pinned IDF queues each complete ring-buffer item atomically or returns zero.
  * Do not emit a record through VFS/newline translation or retry partial bytes. */
 int n=usb_serial_jtag_write_bytes(record,bytes,0);
 if(n==(int)bytes)return 1;
 if(n==0)return 0;
 atomic_store_explicit(&available,false,memory_order_release);return -1;
}
int pocket_qualification_usb_read(uint8_t *out,size_t capacity){
 if(!out||!capacity||capacity>256||!pocket_qualification_usb_available())return -1;
 int n=usb_serial_jtag_read_bytes(out,(uint32_t)capacity,0);
 if(n>=0&&(size_t)n<=capacity)return n;
 atomic_store_explicit(&available,false,memory_order_release);return -1;
}
