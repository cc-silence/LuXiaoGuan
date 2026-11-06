/* Exercise the real upstream netlink parser using synthetic kernel replies. */
#define VERSION_TAG "6.3.4-test"
#define VERSION_YEAR "2024"
#define read fixture_read
#define write fixture_write
#define main hcx_original_main
#include HCX_SOURCE
#undef read
#undef write
#undef main
#include <assert.h>

static unsigned char replies[4096];
static size_t reply_size;
static int consumed;
ssize_t fixture_write(int fd, const void *data, size_t length) {
 (void)fd; (void)data; return length;
}
ssize_t fixture_read(int fd, void *data, size_t length) {
 (void)fd;
 if(consumed++) return 0;
 assert(length>=reply_size);memcpy(data,replies,reply_size);return reply_size;
}
static void attr(struct nlmsghdr *msg,unsigned short type,const void *data,size_t size) {
 struct nlattr *a=(struct nlattr *)((char *)msg+msg->nlmsg_len);
 a->nla_type=type;a->nla_len=sizeof(*a)+size;
 memcpy((char *)a+sizeof(*a),data,size);
 msg->nlmsg_len+=NLA_ALIGN(a->nla_len);
}
static void row(unsigned int phy,unsigned int index,const char *name) {
 struct nlmsghdr *m=(struct nlmsghdr *)(replies+reply_size);
 struct genlmsghdr *g;
 memset(m,0,512);m->nlmsg_type=17;m->nlmsg_len=NLMSG_LENGTH(sizeof(*g));
 g=NLMSG_DATA(m);g->cmd=NL80211_CMD_NEW_INTERFACE;
 attr(m,NL80211_ATTR_WIPHY,&phy,sizeof(phy));
 attr(m,NL80211_ATTR_IFINDEX,&index,sizeof(index));
 attr(m,NL80211_ATTR_IFNAME,name,strlen(name)+1);
 reply_size+=NLMSG_ALIGN(m->nlmsg_len);
}
static void done(void) {
 struct nlmsghdr *m=(struct nlmsghdr *)(replies+reply_size);
 memset(m,0,sizeof(*m));m->nlmsg_type=NLMSG_DONE;m->nlmsg_len=sizeof(*m);
 reply_size+=NLMSG_ALIGN(m->nlmsg_len);
}
static void run(unsigned int selected,int reverse,unsigned int expected) {
 reply_size=0;consumed=0;memset(replies,0,sizeof(replies));
 ifpresentlist=calloc(INTERFACELIST_MAX,sizeof(*ifpresentlist));
 assert(ifpresentlist);ifpresentlist[0].wiphy=0;ifpresentlist[1].wiphy=1;
 ifaktindex=selected;
 if(reverse) {row(0,9,"wlan0");row(0,12,"hcxmon0");}
 else {row(0,12,"hcxmon0");row(0,9,"wlan0");}
 row(1,10,"wlan1");done();
 assert(nl_get_interfacelist());
 if(ifpresentlist[0].index!=expected) {
  fprintf(stderr,"selected %u, reverse %d: got %u expected %u\n",selected,reverse,ifpresentlist[0].index,expected);exit(1);
 }
 assert(ifpresentlist[1].index==10);free(ifpresentlist);
}
int main(void) {
 run(12,0,12);run(12,1,12);
 run(9,0,9);run(9,1,9);
 run(0,0,9);run(0,1,12);
 puts("PASS: selected interface survives either enumeration order; auto-selection and other PHY preserved");
 return 0;
}
