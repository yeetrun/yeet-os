// Copyright (c) 2025 AUTHORS All rights reserved.
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file.

package yeet

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/yeetrun/yeet/pkg/buildinfo"
	"github.com/yeetrun/yeet/pkg/cli"
	"github.com/yeetrun/yeet/pkg/cmdutil"
)

var buildUpgradeReportFn = buildUpgradeReport
var upgradeLocalBinaryFn = upgradeLocalBinary

func HandleUpgrade(ctx context.Context, args []string) error {
	return handleUpgrade(ctx, args, os.Stdout, os.Stderr, buildinfo.Current())
}

func handleUpgrade(ctx context.Context, args []string, stdout io.Writer, stderr io.Writer, local buildinfo.Info) error {
	if len(args) > 0 && args[0] == "upgrade" {
		args = args[1:]
	}
	flags, pos, err := cli.ParseUpgrade(args)
	if err != nil {
		return err
	}
	if flags.Host != "" {
		SetHostOverride(flags.Host)
	}
	checkOnly := flags.Check || len(pos) > 0 && pos[0] == "check"
	var hosts []string
	if checkOnly {
		cfgLoc, _ := loadProjectConfigForCommandFromCwd()
		_, hasHostOverride := HardHostOverride()
		hosts = upgradeKnownHosts(cfgLoc, hasHostOverride)
	} else if onYeetOS() {
		return fmt.Errorf("update the host CLI with sudo yeet-os upgrade")
	}
	report := buildUpgradeReportFn(ctx, upgradeCheckRequest{
		Local:         local,
		Hosts:         hosts,
		Now:           time.Now(),
		Force:         flags.Force,
		Nightly:       flags.Nightly,
		TargetVersion: flags.Version,
	})
	if checkOnly {
		return writeUpgradeCheck(stdout, report, flags.JSON)
	}
	return runUpgrade(stdout, stderr, flags, report)
}

func writeUpgradeCheck(w io.Writer, report upgradeReport, asJSON bool) error {
	if asJSON {
		return json.NewEncoder(w).Encode(report)
	}
	return renderUpgradeReport(w, report)
}

func renderUpgradeReport(w io.Writer, report upgradeReport) error {
	versionHeader := "LATEST"
	if upgradeReportUsesTarget(report) {
		versionHeader = "TARGET"
	}
	rows := make([][]string, 0, len(report.Catch)+1)
	rows = append(rows, upgradeTableRow(report.Local))
	for _, row := range report.Catch {
		rows = append(rows, upgradeTableRow(row))
	}
	return renderOutputTable(w, []string{"COMPONENT", "CURRENT", versionHeader, "STATUS"}, rows)
}

func upgradeReportUsesTarget(report upgradeReport) bool {
	return report.Force || upgradeReportTargetsNightly(report) || strings.TrimSpace(report.TargetVersion) != ""
}

func upgradeReportTargetsNightly(report upgradeReport) bool {
	return report.Nightly || report.Latest.Nightly
}

func upgradeTableRow(row upgradeComponent) []string {
	component := row.Name
	if row.Host != "" {
		component += "@" + row.Host
	}
	current := row.Current
	if current == "" {
		current = "-"
	}
	latest := row.Latest
	if latest == "" {
		latest = "-"
	}
	status := string(row.Status)
	if row.Reason != "" && row.Status != upgradeStatusDev {
		status += ": " + row.Reason
	}
	return []string{component, current, latest, status}
}

func runUpgrade(stdout io.Writer, stderr io.Writer, flags cli.UpgradeFlags, report upgradeReport) error {
	proceed, err := confirmUpgradeIfNeeded(os.Stdin, stdout, stderr, flags, report)
	if err != nil || !proceed {
		return err
	}
	return upgradeLocalFromReport(flags, report)
}

func confirmUpgradeIfNeeded(stdin io.Reader, stdout io.Writer, stderr io.Writer, flags cli.UpgradeFlags, report upgradeReport) (bool, error) {
	if !upgradeReportHasUpdates(report) {
		if _, err := fmt.Fprintln(stdout, "No upgrades available."); err != nil {
			return false, err
		}
		return false, nil
	}
	if flags.Yes {
		return true, nil
	}
	ok, err := confirmUpgradePlan(stdin, stdout, report)
	if err != nil || ok {
		return ok, err
	}
	if _, err := fmt.Fprintln(stderr, "Upgrade cancelled"); err != nil {
		return false, err
	}
	return false, nil
}

func upgradeReportHasUpdates(report upgradeReport) bool {
	return upgradeRowActionable(report.Local)
}

func upgradeRowActionable(row upgradeComponent) bool {
	return row.Status == upgradeStatusUpdateAvailable || row.Status == upgradeStatusReinstall
}

func upgradeLocalFromReport(flags cli.UpgradeFlags, report upgradeReport) error {
	if upgradeRowActionable(report.Local) {
		latest := report.Latest
		if flags.Nightly || upgradeReportTargetsNightly(report) {
			latest.Nightly = true
		}
		if err := upgradeLocalBinaryFn(buildinfo.Current(), latest, flags.Force); err != nil {
			return err
		}
	}
	return nil
}

func confirmUpgradePlan(stdin io.Reader, stdout io.Writer, report upgradeReport) (bool, error) {
	if _, err := fmt.Fprintln(stdout, "Upgrade plan:"); err != nil {
		return false, err
	}
	if upgradeRowActionable(report.Local) {
		if _, err := fmt.Fprintf(stdout, "  yeet: %s -> %s%s\n", report.Local.Current, report.Local.Latest, upgradePlanStatusSuffix(report.Local)); err != nil {
			return false, err
		}
	}
	return cmdutil.Confirm(stdin, stdout, "Proceed?")
}

func upgradePlanStatusSuffix(row upgradeComponent) string {
	if row.Status == upgradeStatusReinstall {
		return " (reinstall release)"
	}
	return ""
}
