#include <Security/Security.h>
#include <CoreFoundation/CoreFoundation.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <mach/message.h>
#include <sys/mount.h>
/* The audit token is obtained from LOCAL_PEERTOKEN, never from the wire. The live bridge
 * remains connected until the reply. Production uses an administrator-pinned
 * Developer ID designated requirement, not a generic Node/Python signature. */
int orca_verify_peer(int socket_fd, const char *requirement) {
    audit_token_t token;
    socklen_t length = sizeof(token);
    if (getsockopt(socket_fd, SOL_LOCAL, LOCAL_PEERTOKEN, &token, &length) || length != sizeof(token)) return -1;
    CFDataRef data = CFDataCreate(NULL, (const UInt8 *)&token, sizeof(token));
    const void *keys[] = { kSecGuestAttributeAudit };
    const void *values[] = { data };
    CFDictionaryRef attrs = CFDictionaryCreate(NULL, keys, values, 1,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    SecCodeRef code = NULL;
    SecRequirementRef req = NULL;
    CFStringRef text = CFStringCreateWithCString(NULL, requirement, kCFStringEncodingUTF8);
    OSStatus status = SecCodeCopyGuestWithAttributes(NULL, attrs, kSecCSDefaultFlags, &code);
    if (!status) status = SecRequirementCreateWithString(text, kSecCSDefaultFlags, &req);
    if (!status) status = SecCodeCheckValidity(code, kSecCSStrictValidate, req);
    if (req) CFRelease(req);
    if (code) CFRelease(code);
    CFRelease(text); CFRelease(attrs); CFRelease(data);
    return (int)status;
}

int orca_local_storage(const char *path) {
    struct statfs info;
    if (statfs(path, &info)) return -1;
    return (info.f_flags & MNT_LOCAL) && !(info.f_flags & MNT_RDONLY) ? 0 : -1;
}
