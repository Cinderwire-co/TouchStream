package cards

/*
#cgo LDFLAGS: -framework Security
#include <stdlib.h>
#include <string.h>
#include <Security/Authorization.h>
#include <Security/AuthorizationTags.h>

// authorize asks for an admin's password, with TouchStream's own prompt, for right (to open a card),
// and gives the authorization in the form authopen takes.
static OSStatus authorize(const char *right, const char *prompt, AuthorizationRef *ref, AuthorizationExternalForm *out) {
	AuthorizationItem item = {right, 0, NULL, 0};
	AuthorizationRights rights = {1, &item};
	AuthorizationItem said = {kAuthorizationEnvironmentPrompt, strlen(prompt), (void *)prompt, 0};
	AuthorizationEnvironment env = {1, &said};
	OSStatus status = AuthorizationCreate(&rights, &env,
		kAuthorizationFlagInteractionAllowed | kAuthorizationFlagExtendRights | kAuthorizationFlagPreAuthorize, ref);
	if (status != errAuthorizationSuccess) return status;
	return AuthorizationMakeExternalForm(*ref, out);
}
*/
import "C"

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

// ErrCanceled: the person said no to the password prompt.
var ErrCanceled = errors.New("the card wasn't written: writing a card needs an admin's password")

// openAsAdmin opens a card to write with an admin's password, through macOS's authopen, which opens it and
// hands the open card back: the app writes it itself, as the person using it. (A program run as admin
// can't: macOS can't ask anyone whether it may use a removable card, so it's refused.)
func openAsAdmin(c Card) (Target, error) {
	right := C.CString("sys.openfile.readwrite." + c.Device)
	prompt := C.CString("TouchStream needs your password to write the spoke's card.")
	defer C.free(unsafe.Pointer(right))
	defer C.free(unsafe.Pointer(prompt))
	var ref C.AuthorizationRef
	var auth C.AuthorizationExternalForm
	switch status := C.authorize(right, prompt, &ref, &auth); status {
	case C.errAuthorizationSuccess:
	case C.errAuthorizationCanceled:
		return nil, ErrCanceled
	default:
		return nil, fmt.Errorf("couldn't get permission to write the card (error %d)", int(status))
	}
	defer C.AuthorizationFree(ref, C.kAuthorizationFlagDefaults)
	form := C.GoBytes(unsafe.Pointer(&auth), C.sizeof_AuthorizationExternalForm)
	for try := 0; ; try++ { // (macOS can mount a card's volumes again at any moment)
		f, err := authopen(c.Device, form)
		if errors.Is(err, ErrBusy) && try < 4 {
			_ = Unmount(c)
			time.Sleep(500 * time.Millisecond)
			continue
		}
		return f, err
	}
}

// authopen runs macOS's authopen with the authorization, and takes the open file it sends back.
func authopen(path string, auth []byte) (*os.File, error) {
	fds, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_STREAM, 0)
	if err != nil {
		return nil, err
	}
	defer syscall.Close(fds[0])
	theirs := os.NewFile(uintptr(fds[1]), "authopen")
	var said bytes.Buffer
	cmd := exec.Command("/usr/libexec/authopen", "-stdoutpipe", "-extauth", "-o", strconv.Itoa(syscall.O_RDWR), path)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = bytes.NewReader(auth), theirs, &said
	err = cmd.Start()
	theirs.Close() // (only authopen has it now: when it's gone, nothing more is coming)
	if err != nil {
		return nil, err
	}
	data, oob := make([]byte, 16), make([]byte, syscall.CmsgSpace(4))
	_, oobn, _, _, recvErr := syscall.Recvmsg(fds[0], data, oob, 0)
	_ = cmd.Wait()
	if recvErr == nil {
		if msgs, err := syscall.ParseSocketControlMessage(oob[:oobn]); err == nil && len(msgs) == 1 {
			if got, err := syscall.ParseUnixRights(&msgs[0]); err == nil && len(got) == 1 {
				return os.NewFile(uintptr(got[0]), path), nil
			}
		}
	}
	why := strings.TrimSpace(said.String())
	switch {
	case strings.Contains(why, "busy"):
		return nil, ErrBusy
	case strings.Contains(why, "not permitted"):
		return nil, errors.New("macOS didn't let the installer use the card: in System Settings → Privacy & Security → " +
			"Files and Folders, turn on Removable Volumes for TouchStream Installer, then try again")
	case why == "":
		why = "authopen gave nothing back"
	}
	return nil, fmt.Errorf("couldn't open the card: %s", why)
}
