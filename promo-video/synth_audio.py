import os
import numpy as np
import soundfile as sf

OUT_WAV = r"F:\Discflow1.0\promo-video\out\discflow_promo_bgm.wav"
SR = 48000
DURATION = 40.0
N_SAMPLES = int(SR * DURATION)
BPM = 96.0
BEAT_SEC = 60.0 / BPM      # 0.625s
BAR_SEC = BEAT_SEC * 4.0   # 2.500s
TOTAL_BARS = 16            # 40.000s


def midi_to_hz(midi: float) -> float:
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def one_pole_lp(x: np.ndarray, cutoff_hz: float) -> np.ndarray:
    """简单高效的一阶低通滤波（支持立体声 Nx2 或单声道 N）。"""
    dt = 1.0 / SR
    rc = 1.0 / (2.0 * np.pi * cutoff_hz)
    alpha = dt / (rc + dt)
    y = np.empty_like(x)
    prev = x[0].copy() if x.ndim == 2 else x[0]
    y[0] = prev
    for i in range(1, len(x)):
        prev = prev + alpha * (x[i] - prev)
        y[i] = prev
    return y


def synth_rhodes_note(freq: float, dur: float, vel: float = 0.8) -> np.ndarray:
    """合成 Warm Rhodes 电钢音色：基频 + 温暖泛音 + 柔和 tine bell 瞬态。"""
    n = int(SR * dur)
    t = np.arange(n, dtype=np.float64) / SR
    # 快速平滑起音消除爆音
    attack = np.minimum(1.0, t / 0.008)
    # 主体温暖共鸣（基频 + 2x + 3x）
    body_env = np.exp(-t / (0.95 + 0.6 * (261.6 / max(freq, 80.0)) ** 0.4))
    body = (
        1.00 * np.sin(2.0 * np.pi * freq * t)
        + 0.24 * np.exp(-t * 2.2) * np.sin(2.0 * np.pi * 2.0 * freq * t)
        + 0.08 * np.exp(-t * 4.0) * np.sin(2.0 * np.pi * 3.0 * freq * t)
    ) * body_env

    # 柔和 tine bell（木槌敲击音叉的清脆泛音）
    bell_env = np.exp(-t / 0.095)
    bell = (
        0.16 * np.sin(2.0 * np.pi * 4.0 * freq * t)
        + 0.09 * np.sin(2.0 * np.pi * 6.98 * freq * t)
    ) * bell_env

    # 释放淡出防止截断
    fade_n = min(n, int(0.06 * SR))
    release = np.ones(n, dtype=np.float64)
    if fade_n > 0:
        release[-fade_n:] = np.linspace(1.0, 0.0, fade_n)

    return vel * attack * release * (body + bell)


def synth_sub_bass(freq: float, dur: float, vel: float = 0.85) -> np.ndarray:
    n = int(SR * dur)
    t = np.arange(n, dtype=np.float64) / SR
    attack = np.minimum(1.0, t / 0.022)
    fade_n = min(n, int(0.08 * SR))
    release = np.ones(n, dtype=np.float64)
    if fade_n > 0:
        release[-fade_n:] = np.linspace(1.0, 0.0, fade_n)
    env = np.exp(-t / 2.2) * attack * release
    sig = (
        0.88 * np.sin(2.0 * np.pi * freq * t)
        + 0.22 * np.sin(2.0 * np.pi * 2.0 * freq * t)
    )
    return vel * env * np.tanh(sig * 1.15)


def synth_kick(vel: float = 0.78) -> np.ndarray:
    n = int(0.32 * SR)
    t = np.arange(n, dtype=np.float64) / SR
    f = 46.0 + (128.0 - 46.0) * np.exp(-t / 0.028)
    phase = 2.0 * np.pi * np.cumsum(f) / SR
    env = np.minimum(1.0, t / 0.004) * np.exp(-t / 0.095)
    thump = np.sin(phase) * env
    click = np.sin(2.0 * np.pi * 165.0 * t) * np.exp(-t / 0.012) * 0.22
    return vel * np.tanh((thump + click) * 1.1)


def synth_shaker(rng: np.random.Generator, vel: float = 0.22) -> np.ndarray:
    n = int(0.085 * SR)
    t = np.arange(n, dtype=np.float64) / SR
    noise = rng.standard_normal(n)
    # 简易高通 + 带通感：一阶差分去低频，再做轻度平滑去刺耳极高频
    hp = np.diff(noise, prepend=0.0)
    bp = one_pole_lp(hp, 8500.0)
    env = np.minimum(1.0, t / 0.006) * np.exp(-t / 0.024)
    return vel * bp * env


def synth_rimshot(vel: float = 0.28) -> np.ndarray:
    n = int(0.09 * SR)
    t = np.arange(n, dtype=np.float64) / SR
    attack = np.minimum(1.0, t / 0.0015)
    sig = (
        0.55 * np.sin(2.0 * np.pi * 780.0 * t) * np.exp(-t / 0.018)
        + 0.35 * np.sin(2.0 * np.pi * 1590.0 * t) * np.exp(-t / 0.013)
        + 0.18 * np.sin(2.0 * np.pi * 2760.0 * t) * np.exp(-t / 0.008)
    )
    return vel * attack * sig


def synth_ui_sfx(kind: str) -> np.ndarray:
    """生成低电平 (-14dB ~ -18dB) 精致 UI 交互微音效（立体声）。"""
    if kind == "sparkle":
        dur = 0.55
        n = int(dur * SR)
        t = np.arange(n, dtype=np.float64) / SR
        sig_l = (
            0.5 * np.sin(2 * np.pi * 1046.5 * t) * np.exp(-t / 0.16)
            + 0.35 * np.sin(2 * np.pi * 1568.0 * np.maximum(0, t - 0.04)) * np.exp(-np.maximum(0, t - 0.04) / 0.18) * (t >= 0.04)
        )
        sig_r = (
            0.35 * np.sin(2 * np.pi * 1046.5 * t) * np.exp(-t / 0.16)
            + 0.5 * np.sin(2 * np.pi * 1568.0 * np.maximum(0, t - 0.04)) * np.exp(-np.maximum(0, t - 0.04) / 0.18) * (t >= 0.04)
        )
        env = np.minimum(1.0, t / 0.005)
        return np.stack([sig_l * env, sig_r * env], axis=1) * 0.14
    elif kind == "whoosh_lift":
        dur = 0.45
        n = int(dur * SR)
        t = np.arange(n, dtype=np.float64) / SR
        rng = np.random.default_rng(101)
        air = one_pole_lp(rng.standard_normal(n), 2200.0)
        env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 1.6
        tone = np.sin(2 * np.pi * (260 + 280 * t) * t) * np.exp(-t / 0.14) * 0.45
        tick = np.sin(2 * np.pi * 1180 * t) * np.exp(-t / 0.015) * 0.35
        l = air * env * 0.25 + tone + tick
        r = np.roll(air, 64) * env * 0.25 + tone * 0.95 + tick
        return np.stack([l, r], axis=1) * 0.16
    elif kind == "popover":
        dur = 0.28
        n = int(dur * SR)
        t = np.arange(n, dtype=np.float64) / SR
        pop = np.sin(2 * np.pi * (420 + 460 * np.exp(-t / 0.02)) * t) * np.exp(-t / 0.032)
        ping = np.sin(2 * np.pi * 1396.9 * np.maximum(0, t - 0.015)) * np.exp(-np.maximum(0, t - 0.015) / 0.09) * (t >= 0.015) * 0.45
        return np.stack([pop + ping * 0.9, pop + ping * 1.1], axis=1) * 0.15
    elif kind == "wipe":
        dur = 0.55
        n = int(dur * SR)
        t = np.arange(n, dtype=np.float64) / SR
        rng = np.random.default_rng(202)
        air = one_pole_lp(rng.standard_normal(n), 3000.0)
        pan = np.linspace(0.0, 1.0, n)
        env = np.sin(np.pi * t / dur) ** 1.4
        chime = np.sin(2 * np.pi * 880.0 * t) * np.exp(-t / 0.20) * 0.35
        return np.stack([air * env * (1 - 0.5 * pan) * 0.35 + chime, air * env * (0.5 + 0.5 * pan) * 0.35 + chime], axis=1) * 0.15
    elif kind == "modal_pop":
        dur = 0.35
        n = int(dur * SR)
        t = np.arange(n, dtype=np.float64) / SR
        thump = np.sin(2 * np.pi * (180 + 260 * np.exp(-t / 0.018)) * t) * np.exp(-t / 0.05) * 0.7
        bell = np.sin(2 * np.pi * 1174.7 * np.maximum(0, t - 0.02)) * np.exp(-np.maximum(0, t - 0.02) / 0.11) * (t >= 0.02) * 0.45
        return np.stack([thump + bell, thump + bell * 1.05], axis=1) * 0.16
    else:  # click_immerse
        dur = 0.48
        n = int(dur * SR)
        t = np.arange(n, dtype=np.float64) / SR
        click = np.sin(2 * np.pi * 920.0 * t) * np.exp(-t / 0.014) * 0.55
        swell = np.sin(2 * np.pi * 622.25 * t) * np.minimum(1.0, t / 0.06) * np.exp(-t / 0.22) * 0.4
        return np.stack([click + swell, click + swell * 0.92], axis=1) * 0.15


def add_stereo_reverb(dry: np.ndarray) -> np.ndarray:
    """多抽头立体声混响 + 柔和阻尼。"""
    taps_l = [(0.029, 0.24), (0.047, 0.20), (0.073, 0.16), (0.107, 0.12), (0.151, 0.08)]
    taps_r = [(0.031, 0.24), (0.053, 0.20), (0.079, 0.16), (0.113, 0.12), (0.163, 0.08)]
    wet = np.zeros_like(dry)
    for d_sec, gain in taps_l:
        d = int(d_sec * SR)
        wet[d:, 0] += dry[:-d, 0] * gain * 0.75 + dry[:-d, 1] * gain * 0.25
    for d_sec, gain in taps_r:
        d = int(d_sec * SR)
        wet[d:, 1] += dry[:-d, 1] * gain * 0.75 + dry[:-d, 0] * gain * 0.25
    wet = one_pole_lp(wet, 3600.0)
    return dry + wet * 0.48


def place_mono(buf: np.ndarray, start_sec: float, sig: np.ndarray, pan: float = 0.0):
    idx = int(round(start_sec * SR))
    if idx >= len(buf):
        return
    end = min(len(buf), idx + len(sig))
    seg = sig[: end - idx]
    gain_l = np.sqrt(0.5 * (1.0 - pan))
    gain_r = np.sqrt(0.5 * (1.0 + pan))
    buf[idx:end, 0] += seg * gain_l
    buf[idx:end, 1] += seg * gain_r


def place_stereo(buf: np.ndarray, start_sec: float, sig: np.ndarray):
    idx = int(round(start_sec * SR))
    if idx >= len(buf):
        return
    end = min(len(buf), idx + len(sig))
    buf[idx:end] += sig[: end - idx]


def main():
    os.makedirs(os.path.dirname(OUT_WAV), exist_ok=True)
    rng = np.random.default_rng(20260401)
    t_all = np.arange(N_SAMPLES, dtype=np.float64) / SR

    # 和弦进行：Fm9 -> Dbmaj9 -> Abmaj9 -> Eb6/9 (每小节 2.5s，4 小节 = 10s 循环)
    chords = [
        ("Fm9",    41, [53, 60, 63, 67, 68, 72]),  # F1 bass, F3 C4 Eb4 G4 Ab4 C5
        ("Dbmaj9", 37, [49, 56, 60, 63, 65, 68]),  # Db1 bass, Db3 Ab3 C4 Eb4 F4 Ab4
        ("Abmaj9", 44, [56, 60, 63, 67, 70, 72]),  # Ab1 bass, Ab3 C4 Eb4 G4 Bb4 C5
        ("Eb6/9",  39, [51, 58, 60, 62, 67, 70]),  # Eb1 bass, Eb3 Bb3 C4 D4 G4 Bb4
    ]

    rhodes_bus = np.zeros((N_SAMPLES, 2), dtype=np.float64)
    pad_bus = np.zeros((N_SAMPLES, 2), dtype=np.float64)
    bass_bus = np.zeros((N_SAMPLES, 2), dtype=np.float64)
    drum_bus = np.zeros((N_SAMPLES, 2), dtype=np.float64)
    sfx_bus = np.zeros((N_SAMPLES, 2), dtype=np.float64)

    # 1. Rhodes + Pad + Bass + Drums 按 16 小节编排
    for bar in range(TOTAL_BARS):
        bar_t = bar * BAR_SEC
        chord_name, bass_midi, voicing = chords[bar % 4]

        # 场景能量分段：
        # Scene 1 (0..5s, bars 0-1): 柔和开篇
        # Scene 2 (5..12.5s, bars 2-4): 鼓组+Sub-bass进入
        # Scene 3 (12.5..20s, bars 5-7): 完整律动 + 柔和旋律点缀
        # Scene 4 (20..25s, bars 8-9): 清亮色彩升华
        # Scene 5 (25..32.5s, bars 10-12): 推进感，准备过渡
        # Scene 6 (32.5..36.5s, bars 13-14): 抽离鼓组，仅留 Rhodes + Pad 沉浸
        # Scene 7 (36.5..40s, bar 14.6-15): 自然收敛余韵

        if bar <= 1:
            rhodes_vel = 0.58 if bar == 0 else 0.68
        elif bar <= 12:
            rhodes_vel = 0.78
        elif bar <= 14:
            rhodes_vel = 0.72
        else:
            rhodes_vel = 0.52

        # Rhodes 和弦轻柔滚奏 (strum 22ms 错峰，最后一小节延长自然衰减)
        chord_dur = 3.6 if bar == 15 else 2.95
        for idx, note_midi in enumerate(voicing):
            strum = idx * 0.022
            pan = -0.25 + 0.5 * (idx / max(1, len(voicing) - 1))
            note_sig = synth_rhodes_note(midi_to_hz(note_midi), chord_dur, vel=rhodes_vel * 0.24)
            place_mono(rhodes_bus, bar_t + strum, note_sig, pan=pan)

        # 第 2 拍半与第 4 拍轻柔 Rhodes 分解点缀（丰富第 2-6 幕音乐性）
        if 2 <= bar <= 14:
            arp_notes = [voicing[-1], voicing[-2], voicing[-3]]
            arp_times = [bar_t + 1.5 * BEAT_SEC, bar_t + 2.5 * BEAT_SEC, bar_t + 3.25 * BEAT_SEC]
            for j, (am, at) in enumerate(zip(arp_notes, arp_times)):
                if at < 37.5:
                    vel_a = 0.14 if bar < 13 else 0.16
                    place_mono(
                        rhodes_bus,
                        at,
                        synth_rhodes_note(midi_to_hz(am + 12), 1.6, vel=vel_a),
                        pan=0.28 * (-1 if j % 2 else 1),
                    )

        # 温暖氛围 Pad（全片铺底，立体声微失谐叠加）
        pad_n = int((BAR_SEC + 0.6) * SR)
        pt = np.arange(pad_n, dtype=np.float64) / SR
        pad_env = np.sin(np.pi * np.clip(pt / (BAR_SEC + 0.6), 0, 1)) ** 0.75
        pad_l = np.zeros(pad_n, dtype=np.float64)
        pad_r = np.zeros(pad_n, dtype=np.float64)
        for nm in voicing[:4]:
            f0 = midi_to_hz(nm)
            pad_l += 0.25 * np.sin(2 * np.pi * f0 * 0.9985 * pt) + 0.08 * np.sin(2 * np.pi * f0 * 2.0 * pt)
            pad_r += 0.25 * np.sin(2 * np.pi * f0 * 1.0015 * pt) + 0.08 * np.sin(2 * np.pi * f0 * 2.0 * pt)
        pad_gain = 0.085 if bar < 2 else (0.12 if bar < 13 else (0.14 if bar < 15 else 0.09))
        place_stereo(pad_bus, bar_t, np.stack([pad_l * pad_env, pad_r * pad_env], axis=1) * pad_gain)

        # Sub-bass（5.0s 起进入，到 36.5s 减弱淡出）
        if 2 <= bar <= 14:
            sub_dur = 2.45 if bar < 13 else 2.7
            sub_vel = 0.42 if bar < 13 else 0.32
            place_mono(bass_bus, bar_t, synth_sub_bass(midi_to_hz(bass_midi), sub_dur, vel=sub_vel), pan=0.0)
            if 3 <= bar <= 12:
                # 第 3 拍半轻柔呼吸低音过门
                place_mono(
                    bass_bus,
                    bar_t + 2.5 * BEAT_SEC,
                    synth_sub_bass(midi_to_hz(bass_midi + 7), 0.9, vel=0.22),
                    pan=0.0,
                )

        # 鼓组（5.0s 起进入，32.5s 精确抽离！）
        # 5.0s = bar 2, 32.5s = bar 13
        if 2 <= bar <= 12:
            # Kick: 第 1 拍、第 3 拍半（柔和 R&B / Lo-fi 呼吸律动）
            for kb in [0.0, 2.5]:
                kt = bar_t + kb * BEAT_SEC
                if kt < 32.35:
                    place_mono(drum_bus, kt, synth_kick(0.56 if kb == 0.0 else 0.44), pan=0.0)
            # Rimshot: 第 2 拍、第 4 拍
            for rb in [1.0, 3.0]:
                rt = bar_t + rb * BEAT_SEC
                if rt < 32.35:
                    place_mono(drum_bus, rt, synth_rimshot(0.24), pan=-0.12)
            # Shaker: 8 分音符微摇摆
            for sb in np.arange(0.0, 4.0, 0.5):
                st = bar_t + sb * BEAT_SEC + (0.014 if int(sb * 2) % 2 == 1 else 0.0)
                if st < 32.35:
                    s_vel = 0.14 if int(sb * 2) % 2 == 0 else 0.09
                    place_mono(drum_bus, st, synth_shaker(rng, s_vel), pan=0.18)
        elif bar == 1:
            # 第 2 小节仅轻柔 Shaker 预告入场
            for sb in [2.0, 2.5, 3.0, 3.5]:
                place_mono(drum_bus, bar_t + sb * BEAT_SEC, synth_shaker(rng, 0.08), pan=0.16)

    # 2. Rhodes 2.4Hz 立体声 Tremolo
    trem_l = 1.0 - 0.18 * (0.5 + 0.5 * np.sin(2.0 * np.pi * 2.4 * t_all))
    trem_r = 1.0 - 0.18 * (0.5 + 0.5 * np.sin(2.0 * np.pi * 2.4 * t_all + np.pi * 0.75))
    rhodes_bus[:, 0] *= trem_l
    rhodes_bus[:, 1] *= trem_r

    # 3. 极轻微黑胶颗粒感（Warm Vinyl Crackle）
    vinyl_base = one_pole_lp(rng.standard_normal((N_SAMPLES, 2)), 1400.0) * 0.0045
    # 稀疏微尘颗粒
    crackles = np.zeros((N_SAMPLES, 2), dtype=np.float64)
    n_pops = 260
    pop_idx = rng.integers(0, N_SAMPLES - 64, size=n_pops)
    for idx in pop_idx:
        ch = rng.integers(0, 2)
        amp = rng.uniform(0.008, 0.022)
        crackles[idx : idx + 6, ch] += amp * np.exp(-np.arange(6) / 1.8)
    vinyl_bus = vinyl_base + one_pole_lp(crackles, 4800.0)

    # 4. 8 个分镜交互踩点微音效 (-14dB ~ -18dB)
    sfx_events = [
        (0.6, "sparkle"),
        (5.0, "whoosh_lift"),
        (12.5, "sparkle"),
        (15.5, "popover"),
        (20.0, "wipe"),
        (25.0, "popover"),
        (28.2, "modal_pop"),
        (32.5, "click_immerse"),
    ]
    for ts, kind in sfx_events:
        place_stereo(sfx_bus, ts, synth_ui_sfx(kind))

    # 5. 分组混音 + 空间混响
    musical_wet = add_stereo_reverb(rhodes_bus * 0.92 + pad_bus * 0.85)
    sfx_wet = add_stereo_reverb(sfx_bus) * 0.45 + sfx_bus * 0.70

    mix = musical_wet + bass_bus + drum_bus * 0.88 + vinyl_bus + sfx_wet

    # 6. 首尾包络：0.0s 18ms 淡入，36.5s–40.0s 自然衰减归零
    fade_in_n = int(0.018 * SR)
    mix[:fade_in_n] *= np.linspace(0.0, 1.0, fade_in_n)[:, None]

    tail_start = int(36.5 * SR)
    tail_len = N_SAMPLES - tail_start
    tail_curve = np.cos(0.5 * np.pi * np.linspace(0.0, 1.0, tail_len)) ** 1.35
    mix[tail_start:] *= tail_curve[:, None]

    # 7. 柔和 tanh 温暖限幅 + -1.0 dBFS (0.89125) 峰值归一化
    warm = np.tanh(mix * 1.15)
    peak_raw = float(np.max(np.abs(warm)))
    if peak_raw <= 1e-9 or not np.isfinite(peak_raw):
        raise RuntimeError("音频信号异常（静音或 NaN/Inf）")

    target_peak = 10.0 ** (-1.0 / 20.0)  # 0.8912509...
    master = (warm * (target_peak / peak_raw)).astype(np.float32)

    assert np.isfinite(master).all(), "Master audio contains NaN/Inf!"
    sf.write(OUT_WAV, master, SR, subtype="PCM_24")

    peak = float(np.max(np.abs(master)))
    rms = float(np.sqrt(np.mean(master ** 2)))
    peak_db = 20.0 * np.log10(peak)
    rms_db = 20.0 * np.log10(rms)
    print(f"Saved: {OUT_WAV}")
    print(f"duration={N_SAMPLES / SR:.3f}s, sr={SR}Hz, channels={master.shape[1]}, samples={N_SAMPLES}")
    print(f"peak={peak:.6f} ({peak_db:.2f} dBFS), rms={rms:.6f} ({rms_db:.2f} dBFS), finite={np.isfinite(master).all()}")


if __name__ == "__main__":
    main()
