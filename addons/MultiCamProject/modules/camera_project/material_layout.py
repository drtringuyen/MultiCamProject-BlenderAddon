"""Where build_material puts each node of MCP_<name> (by node name; frame children are
frame-relative). Taken from the hand-arranged 4-camera material (2026-09-26); cameras 5
and 6 continue below Cam 4 at the cameras' 300 spacing, their weighting nodes follow the
cameras 1-3 pattern (Scale, then Add and Weight Sum 250 to the right). The PROJECTION
frame shrinks to fit, so it grows with them.

A rebuild keeps where a node already stands (see core._place): this table is only for
new materials and for nodes a rebuild adds."""

LAYOUT = {
    "Material Output": (4345.4, -476.9),
    "Principled BSDF": (4030.7, -355.0),

    "ORIGINAL MATERIALS": (3211.0, -710.0),
    "No scan texture": (29.6, -36.1),

    "PROJECTION": (-2536.0, 1486.0),
    "CamUV_1": (34.1, -86.2),
    "CamTex_1": (284.1, -86.2),
    "CamUV_2": (34.1, -386.2),
    "CamTex_2": (284.1, -386.2),
    "CamUV_3": (34.1, -686.2),
    "CamTex_3": (284.1, -686.2),
    "CamUV_4": (30.1, -1203.0),
    "CamTex_4": (280.1, -1203.0),
    "CamUV_5": (30.1, -1503.0),
    "CamTex_5": (280.1, -1503.0),
    "CamUV_6": (30.1, -1803.0),
    "CamTex_6": (280.1, -1803.0),

    # cameras 1-3: VCMix
    "VCMix Attribute": (655.9, -1385.5),
    "VCMix Separate": (1026.1, -1075.0),
    "VCMix R": (1400.3, -1104.8),
    "VCMix G": (1400.3, -1127.9),
    "VCMix B": (1400.3, -1151.5),
    "Cam1 Scale": (1506.0, -36.0),
    "Cam2 Scale": (1506.0, -336.0),
    "Cam2 Add": (1756.0, -336.0),
    "Cam2 Weight Sum": (1756.0, -486.0),
    "Cam3 Scale": (1506.0, -636.0),
    "Cam3 Add": (1756.0, -636.0),
    "Cam3 Weight Sum": (1756.0, -786.0),
    "VCMix Max": (1956.0, -936.0),
    "VCMix Inverse": (2106.0, -936.0),
    "VCMix cameras": (2256.0, -436.0),

    # cameras 4-6: VCMix2
    "VCMix2 Attribute": (1230.2, -1590.3),
    "VCMix2 Separate": (1430.2, -1590.3),
    "Cam4 Scale": (2111.6, -1149.4),
    "Cam5 Scale": (2111.6, -1449.4),
    "Cam5 Add": (2361.6, -1449.4),
    "Cam5 Weight Sum": (2361.6, -1599.4),
    "Cam6 Scale": (2111.6, -1749.4),
    "Cam6 Add": (2361.6, -1749.4),
    "Cam6 Weight Sum": (2361.6, -1899.4),
    "VCMix2 Max": (2843.1, -1168.9),
    "VCMix2 Inverse": (2993.1, -1168.9),
    "VCMix2 cameras": (3277.4, -704.6),

    # 1-3 on top of 4-6
    "1-3 Cover A": (3478.1, -984.9),
    "1-3 Cover": (3628.1, -984.9),
    "4-6 Weight A": (3489.6, -1522.3),
    "4-6 Weight": (3639.6, -1522.3),
    "1-3 Uncovered": (3912.8, -1229.2),
    "4-6 Share": (4145.5, -1275.7),
    "Cameras Cover": (4390.3, -1052.5),
    "Cameras Cover Max": (4591.6, -899.8),
    "4-6 Fraction": (4874.2, -932.6),
    "VCMix on top": (5407.4, -402.4),
    "Blend Mask": (4905.3, -1323.9),

    "BLEND": (2836.0, -400.0),
    "Original Scan": (30.1, -117.5),
    "Projection": (230.1, -117.5),
    "x Blend Mask": (430.1, -117.5),
    "Blend Mix": (748.9, -36.5),
}
