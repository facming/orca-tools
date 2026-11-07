/* Fixed endpoint relay. Pass the original channel, never a claimed PID/token or
 * request bytes. The service authenticates and reads that channel itself. */
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <pwd.h>
#include <errno.h>
#ifndef ORCA_SERVICE_ACCOUNT
#define ORCA_SERVICE_ACCOUNT "_orcatoolkit"
#endif
#ifndef ORCA_SOCKET_DIR
#define ORCA_SOCKET_DIR "/Library/Application Support/OrcaToolkitState/run"
#endif
#define HOST_CHANNEL_FD 3
#define MAX_FRAME (2*1024*1024)
static int output(int fd, const char *p, size_t n) {
    while (n) { ssize_t r=write(fd,p,n); if(r<0 && errno==EINTR)continue;
        if(r<=0)return -1; p+=r;n-=r; } return 0;
}
int orca_relay(int fd) {
    char control[CMSG_SPACE(sizeof(int))]={0}, marker='C';
    struct iovec iov={&marker,1};
    struct msghdr message={0};message.msg_iov=&iov;message.msg_iovlen=1;
    message.msg_control=control;message.msg_controllen=sizeof(control);
    struct cmsghdr *c=CMSG_FIRSTHDR(&message);
    c->cmsg_level=SOL_SOCKET;c->cmsg_type=SCM_RIGHTS;c->cmsg_len=CMSG_LEN(sizeof(int));
    /* fd 3 is an explicit inherited AF_UNIX stream, never stdin. */
    int input=HOST_CHANNEL_FD, type=0; socklen_t length=sizeof(type);
    struct sockaddr_un address; socklen_t address_length=sizeof(address);
    if(input==fd || getsockopt(input,SOL_SOCKET,SO_TYPE,&type,&length) || type!=SOCK_STREAM ||
       getsockname(input,(struct sockaddr*)&address,&address_length) || address.sun_family!=AF_UNIX)
        return 78;
    memcpy(CMSG_DATA(c),&input,sizeof(input));
    if(sendmsg(fd,&message,0)!=1)return 74;
    char *buf=malloc(MAX_FRAME+1);if(!buf)return 72;
    size_t n=0;
    for(;;){ssize_t r=read(fd,buf+n,MAX_FRAME-n);if(r<0 && errno==EINTR)continue;if(r<=0){free(buf);return 75;}n+=r;
        if(buf[n-1]=='\n')break;if(n==MAX_FRAME){free(buf);return 76;}}
    int result=output(STDOUT_FILENO,buf,n)?77:0;free(buf);return result;
}

int main(void) {
    alarm(30); signal(SIGPIPE,SIG_IGN);
    struct passwd *service=getpwnam(ORCA_SERVICE_ACCOUNT);
    if(!service || service->pw_uid==getuid() || service->pw_uid==0)return 70;
    struct sockaddr_un addr={0};addr.sun_family=AF_UNIX;
    snprintf(addr.sun_path,sizeof(addr.sun_path),ORCA_SOCKET_DIR "/%u.sock",getuid());
    int fd=socket(AF_UNIX,SOCK_STREAM,0);uid_t uid;gid_t gid;
    if(fd<0 || connect(fd,(struct sockaddr*)&addr,sizeof(addr)) ||
       getpeereid(fd,&uid,&gid) || uid!=service->pw_uid)return 71;
    return orca_relay(fd);
}
