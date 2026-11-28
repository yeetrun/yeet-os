// Copyright (c) 2025 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package catch

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/yeetrun/yeet/pkg/svc"
)

var (
	catchExecutablePath  = os.Executable
	catchSystemdUnitPath = func(unit string) string {
		return filepath.Join(svc.SystemdUnitDirectory, unit)
	}
	catchSystemdUnitActive = systemdUnitIsActive
	catchSystemctl         = runCatchSystemctl
)

func systemdUnitIsActive(unit string) bool {
	return catchSystemctl("is-active", "--quiet", unit) == nil
}

func runCatchSystemctl(args ...string) error {
	output, err := exec.Command("systemctl", args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("systemctl %s: %w\n%s", strings.Join(args, " "), err, string(output))
	}
	return nil
}
