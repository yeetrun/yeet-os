// Copyright (c) 2025 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package svc

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strings"
	"sync"
)

// NixOS provides an additional unit search directory owned exclusively by Catch.
func managedUnitTree() bool { return SystemdUnitDirectory != "/etc/systemd/system" }

type systemctlFunc func(...string) error

// ReloadAndEnableUnits publishes the boot dependencies alongside the staged units,
// then lets PID 1 load the complete graph in a single reload.
func ReloadAndEnableUnits(run systemctlFunc, units ...string) error {
	return publishBootUnits(run, units, nil, true)
}

func publishBootUnits(run systemctlFunc, units, disabled []string, definitionsChanged bool) error {
	if managedUnitTree() {
		return publishManagedUnits(run, units, disabled, definitionsChanged)
	}
	if err := run("daemon-reload"); err != nil {
		return fmt.Errorf("failed to reload systemd: %w", err)
	}
	for _, name := range units {
		if err := run("enable", name); err != nil {
			return fmt.Errorf("failed to enable %s: %w", name, err)
		}
	}
	return nil
}

func publishManagedUnits(run systemctlFunc, units, disabled []string, definitionsChanged bool) error {
	changes := make([]bootLinkChange, 0, len(units)+len(disabled))
	for _, name := range units {
		changes = append(changes, bootLinkChange{name: name, enable: true})
	}
	for _, name := range disabled {
		changes = append(changes, bootLinkChange{name: name})
	}
	linksChanged, err := applyBootLinks(changes)
	if err != nil {
		return err
	}
	if !definitionsChanged && !linksChanged && len(units) == 1 && strings.HasSuffix(units[0], ".service") && liveBootUnitCurrent(units[0]) {
		return nil
	}
	return run("daemon-reload")
}

func EnableUnit(run systemctlFunc, name string, now bool) error {
	return changeUnitBootState(run, name, true, now)
}

func DisableUnit(run systemctlFunc, name string, now bool) error {
	return changeUnitBootState(run, name, false, now)
}

func changeUnitBootState(run systemctlFunc, name string, enable, now bool) error {
	op, action := "disable", "stop"
	if enable {
		op, action = "enable", "start"
	}
	if !managedUnitTree() {
		if now {
			return run(op, "--now", name)
		}
		return run(op, name)
	}
	if _, err := setBootLinks(name, enable); err != nil {
		return err
	}
	// Reload on explicit enable/disable even on retry: a previous reload may
	// have failed after the links were published.
	if err := run("daemon-reload"); err != nil {
		return fmt.Errorf("failed to reload systemd: %w", err)
	}
	if now {
		return run(action, name)
	}
	return nil
}

// UnitBootEnabled reads the same links that Catch writes. Use the system
// manager for host-provided units such as the declarative catch.service.
func UnitBootEnabled(name string, systemState func() (bool, error)) (bool, error) {
	if !managedUnitTree() {
		return systemState()
	}
	if !validBootUnit(name) {
		return false, fmt.Errorf("invalid unit name %q", name)
	}
	if _, err := os.Lstat(filepath.Join(SystemdUnitDirectory, name)); errors.Is(err, os.ErrNotExist) {
		return systemState()
	} else if err != nil {
		return false, err
	}
	bootLinksMu.Lock()
	defer bootLinksMu.Unlock()
	links, err := existingBootLinks(name)
	return len(links) > 0, err
}

func validBootUnit(name string) bool {
	return filepath.IsLocal(name) && filepath.Base(name) == name && name != "." &&
		!strings.ContainsAny(name, " \t\r\n\x00")
}

// Catch renders [Install] itself. Support the dependency directives it emits,
// including multiple targets on the same line and repeated/reset assignments.
func installBootLinks(name string) ([]string, error) {
	raw, err := os.ReadFile(filepath.Join(SystemdUnitDirectory, name))
	if err != nil {
		return nil, err
	}
	var wants, requires []string
	inInstall := false
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "[") {
			inInstall = line == "[Install]"
			continue
		}
		if !inInstall || line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			continue
		}
		if err := addInstallDependency(name, line, &wants, &requires); err != nil {
			return nil, err
		}
	}
	links := append(wants, requires...)
	slices.Sort(links)
	return slices.Compact(links), nil
}

func addInstallDependency(name, line string, wants, requires *[]string) error {
	key, value, _ := strings.Cut(line, "=")
	var links *[]string
	var suffix string
	switch strings.TrimSpace(key) {
	case "WantedBy":
		links, suffix = wants, ".wants"
	case "RequiredBy":
		links, suffix = requires, ".requires"
	default:
		return fmt.Errorf("unsupported [Install] directive %q on %s", key, name)
	}
	if strings.TrimSpace(value) == "" {
		*links = nil
	}
	for _, target := range strings.Fields(value) {
		if !validBootUnit(target) {
			return fmt.Errorf("invalid boot target %q for %s", target, name)
		}
		*links = append(*links, filepath.Join(target+suffix, name))
	}
	return nil
}

func existingBootLinks(name string) ([]string, error) {
	entries, err := os.ReadDir(SystemdUnitDirectory)
	if err != nil {
		return nil, err
	}
	var links []string
	for _, dir := range entries {
		if !dir.IsDir() || !strings.HasSuffix(dir.Name(), ".wants") && !strings.HasSuffix(dir.Name(), ".requires") {
			continue
		}
		link := filepath.Join(dir.Name(), name)
		dest, err := os.Readlink(filepath.Join(SystemdUnitDirectory, link))
		if errors.Is(err, os.ErrNotExist) {
			continue
		}
		if err != nil || dest != "../"+name {
			return nil, fmt.Errorf("unexpected boot link %s for %s (target %q, error %v)", link, name, dest, err)
		}
		links = append(links, link)
	}
	return links, nil
}

var bootLinksMu sync.Mutex

type bootLinkChange struct {
	name         string
	enable       bool
	old, desired []string
}

func setBootLinks(name string, enable bool) (bool, error) {
	return applyBootLinks([]bootLinkChange{{name: name, enable: enable}})
}

// Validate the full batch before publishing. Restore earlier changes if an
// individual filesystem operation fails; reconciliation is also safe to retry.
func applyBootLinks(changes []bootLinkChange) (bool, error) {
	bootLinksMu.Lock()
	defer bootLinksMu.Unlock()
	for i := range changes {
		change := &changes[i]
		if !validBootUnit(change.name) {
			return false, fmt.Errorf("invalid unit name %q", change.name)
		}
		var err error
		change.old, err = existingBootLinks(change.name)
		if err != nil {
			return false, err
		}
		if change.enable {
			change.desired, err = installBootLinks(change.name)
			if err != nil {
				return false, err
			}
		}
	}
	var changed bool
	for i, change := range changes {
		didChange, err := reconcileBootLinks(change.name, change.old, change.desired)
		if err != nil {
			err = errors.Join(err, restoreBootLinks(changes[:i+1]))
			return false, err
		}
		changed = changed || didChange
	}
	return changed, nil
}

func restoreBootLinks(changes []bootLinkChange) error {
	var err error
	for j := len(changes) - 1; j >= 0; j-- {
		prior := changes[j]
		links, restoreErr := existingBootLinks(prior.name)
		if restoreErr == nil {
			_, restoreErr = reconcileBootLinks(prior.name, links, prior.old)
		}
		err = errors.Join(err, restoreErr)
	}
	return err
}

// A matching file alone cannot prove PID 1 loaded it (for example after an
// interrupted deploy). On a simple payload-only update verify the live graph.
func liveBootUnitCurrent(name string) bool {
	out, err := exec.Command("systemctl", "show", "--property=LoadState,NeedDaemonReload,FragmentPath,WantedBy,RequiredBy", name).Output()
	if err != nil {
		return false
	}
	props := make(map[string]string)
	for _, line := range strings.Split(string(out), "\n") {
		k, v, ok := strings.Cut(line, "=")
		if ok {
			props[k] = v
		}
	}
	if props["LoadState"] != "loaded" || props["NeedDaemonReload"] != "no" || props["FragmentPath"] != filepath.Join(SystemdUnitDirectory, name) {
		return false
	}
	desired, err := installBootLinks(name)
	if err != nil {
		return false
	}
	var live []string
	for key, suffix := range map[string]string{"WantedBy": ".wants", "RequiredBy": ".requires"} {
		for _, target := range strings.Fields(props[key]) {
			live = append(live, filepath.Join(target+suffix, name))
		}
	}
	slices.Sort(live)
	slices.Sort(desired)
	return slices.Equal(live, desired)
}

func reconcileBootLinks(name string, old, desired []string) (bool, error) {
	var changed bool
	for _, link := range desired {
		if slices.Contains(old, link) {
			continue
		}
		if err := createBootLink(name, link); err != nil {
			return changed, err
		}
		changed = true
	}
	for _, link := range old {
		if !slices.Contains(desired, link) {
			if err := os.Remove(filepath.Join(SystemdUnitDirectory, link)); err != nil {
				return changed, err
			}
			changed = true
		}
	}
	return changed, nil
}

func createBootLink(name, link string) error {
	path := filepath.Join(SystemdUnitDirectory, link)
	dir := filepath.Dir(path)
	if err := os.Mkdir(dir, 0755); err != nil && !errors.Is(err, os.ErrExist) {
		return err
	}
	if info, err := os.Lstat(dir); err != nil || !info.IsDir() {
		return fmt.Errorf("invalid boot link directory %s", dir)
	}
	if err := os.Symlink("../"+name, path); err != nil {
		return err
	}
	return nil
}
