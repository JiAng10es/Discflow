# Bottom Dock Symmetry (Style 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform `.bottom-dock-player` into a 100% horizontally centered, symmetric 3-column CSS Grid layout (Style 1), eliminating the ~79px rightward offset while preserving existing controls, passing all unit tests, and updating PRD.md.

**Architecture:** Switch `.bottom-dock-player` from flex to `display: grid; grid-template-columns: minmax(160px, 220px) 1fr minmax(160px, 220px);`. Left wing aligns to start, center controls cluster aligns strictly to center (`justify-self: center`), and right actions align to end (`justify-self: end`).

**Tech Stack:** HTML5 / CSS3 (in `win-cd-player/main.py`), Python 3.12, unittest.

**Spec:** [dock-symmetry-design.md](file:///F:/Discflow1.0/docs/superpowers/specs/2026-11-03-dock-symmetry-design.md)

## Global Constraints
- Target workspace: `F:\Discflow1.0` (do NOT touch `E:\Discflow`).
- UTF-8 + CRLF encoding for any script changes.
- Avoid breaking any of the existing 163 unit tests.
- Update `F:\Discflow1.0\PRD.md` with v2.0.1 details upon completion.

## Review Focus
- Layout distortion on small windows (<600px): ensured by `minmax` clamp and `overflow: hidden`.
- Track title text overflow ellipsis in `.dock-track-title`: must not break symmetry.
- Compatibility with `test_v20_cleanup.py` and other layout tests.
- Verify node syntax and runtime HTML integrity.

---

### Task 1: Add Unit Tests for Symmetric Dock Layout

**Files:**
- Create: `F:\Discflow1.0\win-cd-player\test_dock_symmetry.py`

- [ ] **Step 1: Write test cases in `test_dock_symmetry.py`**
  Assert that:
  - `.bottom-dock-player` defines `grid-template-columns` with symmetric wings (`minmax(160px, 220px)` or equivalent).
  - `.dock-center-cluster` has `justify-self: center`.
  - `.dock-album-cluster` has `justify-self: start`.
  - `.dock-right-actions` has `justify-self: end`.
  - `APPLE_MUSIC_HTML` still passes JavaScript syntax checks (`node --check` if available).

- [ ] **Step 2: Run test to confirm it fails initially**
  ```bash
  Set-Location "F:\Discflow1.0"; python -m unittest win-cd-player/test_dock_symmetry.py
  ```

---

### Task 2: Implement Style 1 CSS in `win-cd-player/main.py`

**Files:**
- Modify: `F:\Discflow1.0\win-cd-player\main.py` (lines ~2540-2675)

- [ ] **Step 1: Edit `.bottom-dock-player`, `.dock-album-cluster`, `.dock-center-cluster`, and `.dock-right-actions` CSS rules**
  - Add `display: grid; grid-template-columns: minmax(160px, 220px) 1fr minmax(160px, 220px);`
  - Update `.dock-album-cluster` to `justify-self: start; width: 100%; max-width: 220px; flex: 0 1 220px;`
  - Update `.dock-center-cluster` to `justify-self: center; width: 100%; max-width: 380px;`
  - Update `.dock-right-actions` to `justify-self: end; width: 100%; max-width: 220px; flex: 0 0 auto; justify-content: flex-end;`
  - Ensure padding and gap maintain harmonious spacing (`padding: 0 24px; gap: 16px;`).

- [ ] **Step 2: Run `test_dock_symmetry.py` and ensure it passes**
  ```bash
  Set-Location "F:\Discflow1.0"; python -m unittest win-cd-player/test_dock_symmetry.py
  ```

---

### Task 3: Full Test Suite Verification

**Files:**
- Test: All test suites in `F:\Discflow1.0\win-cd-player`

- [ ] **Step 1: Run complete test suite**
  ```bash
  Set-Location "F:\Discflow1.0"; python -m unittest discover -s win-cd-player -p "test_*.py"
  ```
  Verify all 164+ tests pass without any failures or regressions.

---

### Task 4: Documentation & PRD Update

**Files:**
- Modify: `F:\Discflow1.0\PRD.md`

- [ ] **Step 1: Update `PRD.md` header and append Section for v2.0.1**
  - Update header version to `v2.0.1`.
  - Add section documenting the bottom dock symmetry optimization (Style 1: 3-column CSS Grid centered layout).

- [ ] **Step 2: Commit changes to Git repository**
  ```bash
  git -C "F:\Discflow1.0" add win-cd-player/main.py win-cd-player/test_dock_symmetry.py PRD.md docs/superpowers/
  git -C "F:\Discflow1.0" commit -m "fix(ui): 优化底部微播控 Dock 栏为现代三段式 Grid 居中对称布局 (v2.0.1)"
  ```
