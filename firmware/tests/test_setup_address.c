#include <assert.h>
#include <string.h>
#include "../main/pocket_setup_address.h"
int main(void) {
 struct sockaddr_storage storage; memset(&storage,0,sizeof(storage));
 struct sockaddr_in *v4=(struct sockaddr_in*)&storage;
 v4->sin_family=AF_INET; v4->sin_addr.s_addr=htonl(0xc0a80401);
 assert(pocket_setup_address_matches(&storage,sizeof(*v4)));
 assert(!pocket_setup_address_matches(&storage,sizeof(*v4)-1));
 v4->sin_addr.s_addr=htonl(0xc0a80152);
 assert(!pocket_setup_address_matches(&storage,sizeof(*v4)));
 memset(&storage,0,sizeof(storage));
 struct sockaddr_in6 *v6=(struct sockaddr_in6*)&storage;
 v6->sin6_family=AF_INET6;
 unsigned char mapped[16]={0,0,0,0,0,0,0,0,0,0,255,255,192,168,4,1};
 memcpy(&v6->sin6_addr,mapped,16);
 assert(pocket_setup_address_matches(&storage,sizeof(*v6)));
 assert(!pocket_setup_address_matches(&storage,sizeof(*v6)-1));
 mapped[15]=2;memcpy(&v6->sin6_addr,mapped,16);
 assert(!pocket_setup_address_matches(&storage,sizeof(*v6)));
 mapped[15]=1;mapped[10]=0;memcpy(&v6->sin6_addr,mapped,16);
 assert(!pocket_setup_address_matches(&storage,sizeof(*v6)));
 memset(&storage,0,sizeof(storage));
 assert(!pocket_setup_address_matches(&storage,sizeof(storage)));
 assert(!pocket_setup_address_matches(NULL,0));
 return 0;
}
