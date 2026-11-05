/* Unprivileged transport fixture ONLY: real two-hop code authentication with a
 * test server on the current UID. Production main keeps its distinct UID gate. */
#define main production_bridge_main
#include "bridge.c"
#undef main
int main(void) {
    alarm(10);signal(SIGPIPE,SIG_IGN);
    struct sockaddr_un addr={0};addr.sun_family=AF_UNIX;
    snprintf(addr.sun_path,sizeof(addr.sun_path),ORCA_SOCKET_DIR "/%u.sock",getuid());
    int fd=socket(AF_UNIX,SOCK_STREAM,0);
    if(fd<0 || connect(fd,(struct sockaddr*)&addr,sizeof(addr)))return 71;
    return orca_relay(fd);
}
