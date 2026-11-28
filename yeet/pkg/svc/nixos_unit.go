// Copyright (c) 2026 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package svc

import (
	"fmt"
	"strings"
)

// NixSandboxLauncher is set by the NixOS package.
var NixSandboxLauncher string

// AdaptHostUnit translates the fixed runtime mounts from Debian generations.
// Catch calls this for every render, including reconciliation and rollback.
func AdaptHostUnit(raw string) (string, error) {
	if NixSandboxLauncher == "" || !strings.Contains(raw, "ExecStart=/usr/bin/bwrap ") {
		return raw, nil
	}
	const old = " --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /sbin /sbin --ro-bind /lib /lib --ro-bind /lib64 /lib64 "
	const packaged = " --ro-bind /etc/yeet-os/sandbox-bin /bin --ro-bind /etc/yeet-os/sandbox-bin /usr/bin --ro-bind /etc/yeet-os/sandbox-lib /lib --ro-bind /etc/yeet-os/sandbox-lib /lib64 --setenv PATH /usr/bin:/bin --setenv LD_LIBRARY_PATH /lib "
	lines := strings.Split(raw, "\n")
	for i, line := range lines {
		command, ok := strings.CutPrefix(line, "ExecStart=/usr/bin/bwrap ")
		if !ok {
			continue
		}
		head, payload, ok := strings.Cut(command, " -- ")
		if !ok || strings.Count(head, old) != 1 {
			return "", fmt.Errorf("unsupported legacy bubblewrap runtime mount layout")
		}
		head = strings.Replace(head, old, packaged, 1)
		for _, p := range []string{"/etc/ld.so.cache", "/etc/ld.so.conf", "/etc/ld.so.conf.d", "/etc/timezone", "/etc/ssl/openssl.cnf", "/etc/ca-certificates.conf"} {
			head = strings.Replace(head, " --ro-bind "+p+" "+p+" ", " --ro-bind-try "+p+" "+p+" ", 1)
		}
		lines[i] = "ExecStart=" + NixSandboxLauncher + " " + head + " -- " + payload
	}
	return strings.Join(lines, "\n"), nil
}
