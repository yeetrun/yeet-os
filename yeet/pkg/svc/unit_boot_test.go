// Copyright (c) 2025 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package svc

import (
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/yeetrun/yeet/pkg/db"
)

func testBootTree(t *testing.T) string {
	t.Helper()
	old := SystemdUnitDirectory
	SystemdUnitDirectory = t.TempDir()
	t.Cleanup(func() { SystemdUnitDirectory = old })
	return SystemdUnitDirectory
}

func TestUnitBootDependencies(t *testing.T) {
	dir := testBootTree(t)
	write := func(name, text string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), []byte(text), 0644); err != nil {
			t.Fatal(err)
		}
	}
	write("job.service", "[Install]\nWantedBy=multi-user.target\n")
	write("job.timer", "[Timer]\nOnCalendar=daily\n[Install]\nWantedBy=old.target\nWantedBy=\nWantedBy=timers.target other.target\nWantedBy=other.target\nRequiredBy=dep.target\n")
	var calls []string
	run := func(args ...string) error { calls = append(calls, strings.Join(args, " ")); return nil }
	check := func(name string, want ...string) {
		t.Helper()
		got, err := existingBootLinks(name)
		if err != nil || !slices.Equal(got, want) {
			t.Fatalf("links of %s = %v, %v; want %v", name, got, err, want)
		}
	}
	if err := EnableUnit(run, "job.service", false); err != nil {
		t.Fatal(err)
	}
	if err := publishBootUnits(run, []string{"job.timer"}, []string{"job.service"}, true); err != nil {
		t.Fatal(err)
	}
	check("job.service")
	check("job.timer", "dep.target.requires/job.timer", "other.target.wants/job.timer", "timers.target.wants/job.timer")
	noFallback := func() (bool, error) { t.Fatal("queried PID 1 for a managed unit"); return false, nil }
	on, err := UnitBootEnabled("job.timer", noFallback)
	if !on || err != nil {
		t.Fatalf("timer enabled = %t, %v", on, err)
	}
	on, err = UnitBootEnabled("job.service", noFallback)
	if on || err != nil {
		t.Fatalf("service enabled = %t, %v", on, err)
	}
	if err := DisableUnit(run, "job.timer", true); err != nil {
		t.Fatal(err)
	}
	check("job.timer")
	if err := EnableUnit(run, "job.service", true); err != nil {
		t.Fatal(err)
	}
	check("job.service", "multi-user.target.wants/job.service")
	if !slices.Equal(calls, []string{"daemon-reload", "daemon-reload", "daemon-reload", "stop job.timer", "daemon-reload", "start job.service"}) {
		t.Fatal(calls)
	}
	on, err = UnitBootEnabled("catch.service", func() (bool, error) { return true, nil })
	if !on || err != nil {
		t.Fatal(on, err)
	}
}

func TestBootLinkFailureRestoresPriorState(t *testing.T) {
	dir := testBootTree(t)
	for _, name := range []string{"first.service", "second.service"} {
		target := "ok.target"
		if name == "second.service" {
			target = "blocked.target"
		}
		if err := os.WriteFile(filepath.Join(dir, name), []byte("[Install]\nWantedBy="+target+"\n"), 0644); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink(t.TempDir(), filepath.Join(dir, "blocked.target.wants")); err != nil {
		t.Fatal(err)
	}
	err := ReloadAndEnableUnits(func(...string) error { t.Fatal("reloaded after unsuccessful publication"); return nil }, "first.service", "second.service")
	if err == nil {
		t.Fatal("accepted symlinked dependency directory")
	}
	if _, err := os.Lstat(filepath.Join(dir, "ok.target.wants", "first.service")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("partial install: %v", err)
	}
	if _, err := setBootLinks("../escape.service", false); err == nil {
		t.Fatal("accepted path traversal")
	}
	if _, err := os.Stat(filepath.Join(dir, "blocked.target.wants", "second.service")); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("wrote outside unit tree")
	}
}

func TestBootLinksRejectInvalidInstallData(t *testing.T) {
	dir := testBootTree(t)
	for _, text := range []string{"[Install]\nAlias=x.service", "[Install]\nWantedBy=../outside.target"} {
		if err := os.WriteFile(filepath.Join(dir, "app.service"), []byte(text), 0644); err != nil {
			t.Fatal(err)
		}
		if _, err := setBootLinks("app.service", true); err == nil {
			t.Fatalf("accepted %q", text)
		}
	}
	if err := os.Mkdir(filepath.Join(dir, "timers.target.wants"), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("/foreign", filepath.Join(dir, "timers.target.wants", "app.service")); err != nil {
		t.Fatal(err)
	}
	if _, err := setBootLinks("app.service", false); err == nil {
		t.Fatal("removed foreign link")
	}
}

func TestPayloadOnlyDeployChecksLiveGraph(t *testing.T) {
	dir := testBootTree(t)
	name := "app.service"
	if err := os.WriteFile(filepath.Join(dir, name), []byte("[Install]\nWantedBy=multi-user.target\n"), 0644); err != nil {
		t.Fatal(err)
	}
	bin := t.TempDir()
	fake := "#!/bin/sh\nprintf '%s\\n' \"$TEST_BOOT_GRAPH\"\n"
	if err := os.WriteFile(filepath.Join(bin, "systemctl"), []byte(fake), 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin)
	good := "LoadState=loaded\nNeedDaemonReload=no\nFragmentPath=" + filepath.Join(dir, name) + "\nWantedBy=multi-user.target\nRequiredBy="
	calls := 0
	run := func(...string) error { calls++; return nil }
	for _, tc := range []struct {
		graph string
		want  int
	}{
		{good, 1}, // First publish changes the boot links.
		{good, 0}, // Second publish has identical definitions and live graph.
		{strings.Replace(good, "=no", "=yes", 1), 1},
		{strings.Replace(good, "WantedBy=multi-user.target", "WantedBy=", 1), 1},
		{strings.Replace(good, dir, "/other", 1), 1},
	} {
		t.Setenv("TEST_BOOT_GRAPH", tc.graph)
		calls = 0
		if err := publishBootUnits(run, []string{name}, nil, false); err != nil {
			t.Fatal(err)
		}
		if calls != tc.want {
			t.Fatalf("reloaded %d times, want %d for %q", calls, tc.want, tc.graph)
		}
	}
}

func TestInstalledUnitMatchesOnlySafeIdenticalFiles(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "job.service")
	step := installStep{artifact: db.ArtifactSystemdUnit, artifactInstall: artifactInstall{dstPath: path}}
	want := []byte("[Service]\nExecStart=/bin/true\n")
	matches := func(raw []byte, mode os.FileMode, expected bool) {
		t.Helper()
		if got := installedUnitMatches(step, raw, mode); got != expected {
			t.Fatalf("matches = %v, want %v for %q, %v", got, expected, raw, mode)
		}
	}
	matches(want, 0644, false)
	if err := os.WriteFile(path, want, 0644); err != nil {
		t.Fatal(err)
	}
	matches(want, 0644, true)
	matches(want, 0600, false)
	matches([]byte("different"), 0644, false)
	matches([]byte(strings.Replace(string(want), "true", "nope", 1)), 0644, false)
	other := filepath.Join(dir, "hardlink")
	if err := os.Link(path, other); err != nil {
		t.Fatal(err)
	}
	matches(want, 0644, false)
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(other, path); err != nil {
		t.Fatal(err)
	}
	matches(want, 0644, false)
}
