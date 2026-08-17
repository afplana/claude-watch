class ClaudeWatch < Formula
  desc "Santa-safe menu bar live feed + alerts for Claude Code sessions"
  homepage "https://github.com/afplana/claude-watch"
  url "https://github.com/afplana/claude-watch/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "b7d10d28c3776d51ea3b1b3df8c01dfdeaa06e4a687e64c310eac3010790c9e6"
  license "MIT"

  # No compiled binaries shipped by us: scripts run under Apple-signed
  # /usr/bin/python3. PyObjC is vendored as prebuilt CPython wheels for that
  # interpreter — still Santa-safe (Santa evaluates the approved interpreter).
  depends_on :macos

  # Wheels match /usr/bin/python3 on macOS (CLT Python 3.9, universal2).
  resource "pyobjc-core" do
    url "https://files.pythonhosted.org/packages/0b/3c/98f04333e4f958ee0c44ceccaf0342c2502d361608e00f29a5d50e16a569/pyobjc_core-11.1-cp39-cp39-macosx_10_9_universal2.whl"
    sha256 "4a99e6558b48b8e47c092051e7b3be05df1c8d0617b62f6fa6a316c01902d157"
  end

  resource "pyobjc-framework-Cocoa" do
    url "https://files.pythonhosted.org/packages/b2/9b/5499d1ed6790b037b12831d7038eb21031ab90a033d4cfa43c9b51085925/pyobjc_framework_cocoa-11.1-cp39-cp39-macosx_10_9_universal2.whl"
    sha256 "bbee71eeb93b1b31ffbac8560b59a0524a8a4b90846a260d2c4f2188f3d4c721"
  end

  def install
    libexec.install "hook.py", "bar.py", "cw.py", "cli.py", "install.py", "uninstall.py"
    vendor = libexec/"vendor"
    vendor.mkpath
    resources.each do |r|
      r.stage do
        system "unzip", "-qo", Dir["*.whl"].first, "-d", vendor
      end
    end
    (bin/"claude-watch").write <<~SH
      #!/bin/bash
      export PYTHONPATH="#{vendor}:${PYTHONPATH}"
      exec /usr/bin/python3 "#{libexec}/cli.py" "$@"
    SH
  end

  def caveats
    <<~EOS
      One-time activation (registers the Claude Code hooks and starts the
      menu bar app at login):

        claude-watch install

      Then restart any running Claude Code sessions so they pick up the hooks.
      Remove everything with:  claude-watch uninstall   (add --purge to delete data)
    EOS
  end

  test do
    assert_match "claude-watch #{version}", shell_output("#{bin}/claude-watch version")
    assert_equal "ok", shell_output(
      "PYTHONPATH=#{libexec}/vendor /usr/bin/python3 -c 'import AppKit; print(\"ok\")'"
    ).strip
  end
end
