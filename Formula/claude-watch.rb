class ClaudeWatch < Formula
  desc "Santa-safe menu bar live feed + alerts for Claude Code sessions"
  homepage "https://github.com/afplana/claude-watch"
  url "https://github.com/afplana/claude-watch/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "REPLACE_WITH_TARBALL_SHA256"
  license "MIT"

  # No build step and no bundled binaries: the tool runs entirely under the
  # Apple-signed system /usr/bin/python3 (which already ships PyObjC), so there
  # is nothing for a corporate Santa Team-ID rule to block.
  depends_on :macos

  def install
    libexec.install "hook.py", "bar.py", "cw.py", "cli.py", "install.py", "uninstall.py"
    (bin/"claude-watch").write <<~SH
      #!/bin/bash
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
  end
end
