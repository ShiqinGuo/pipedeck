"""Pipedeck: scattered source becomes a connected, testable local application."""

import math
from pathlib import Path

from motion import Canvas, ease, lerp, render, spring

BLUE, GREEN = "#496AE8", "#269B80"


def canvas():
    return Canvas("#F4F6FB", "#303746", "#788296", BLUE, "#E4EAFE", "#DCE2EE")


def module(c, x, y, name, detail, ready=False, progress=None, width=218):
    c.card(x, y, width, 152)
    c.cube(x + 38, y + 36)
    c.text(x + 20, y + 78, name, 22, bold=True)
    c.text(x + 20, y + 113, detail, 14, c.muted)
    if ready:
        c.circle(x + width - 26, y + 28, 14, "#E2F4EE")
        c.tick(x + width - 26, y + 28, GREEN)
    elif progress is not None:
        c.line([(x + 77, y + 41), (x + width - 23, y + 41)], c.edge, 4)
        if progress > 0:
            c.line([(x + 77, y + 41), (x + 77 + (width - 100) * progress, y + 41)], BLUE, 4)


def scene(t):
    c = canvas()
    if t < 8:
        c.chrome(
            "PIPEDECK",
            "Several repositories. One local workspace.",
            1,
            "Bring code, commands and connections into the same plan.",
            t,
        )
        p = ease((t - 1.1) / 2.2)
        c.rect((73, 175, 1046, 482), "#EEF1F9", 28, "#D6DEEE")
        c.text(98, 192, "WORKSPACE / local integration", 13, c.muted, True)
        x1, y1 = lerp(33, 126, p), lerp(239, 258, p)
        x2, y2 = lerp(476, 450, p), lerp(150, 258, p)
        x3, y3 = lerp(858, 774, p), lerp(342, 258, p)
        for a, b in [((x1 + 218, y1 + 75), (x2, y2 + 75)), ((x2 + 218, y2 + 75), (x3, y3 + 75))]:
            if t > 3.3:
                c.line([a, b])
                c.packet([a, b], ease((t - 3.3) / 1))
        module(c, x1, y1, "Frontend", "feature / app-ui")
        module(c, x2, y2, "Backend", "feature / app-api")
        module(c, x3, y3, "Data connection", "local configuration")
        if t > 4.5:
            c.pill(374, 442, "Preflight", True)
            c.pill(489, 442, "Build", t > 5.3)
            c.pill(579, 442, "Start locally", t > 6.1)
    elif t < 16:
        q = t - 8
        c.chrome(
            "PIPEDECK",
            "Ready in the right order. Not just running.",
            2,
            "Verify dependencies, then start downstream services.",
            t,
        )
        xs = [105, 452, 799]
        names = ["Data connection", "Backend", "Frontend"]
        detail = ["Probe local dependency", "running / 82c1f4", "running / 7a03d2"]
        starts = [0.4, 2.3, 4.2]
        for i in range(2):
            path = [(xs[i] + 218, 326), (xs[i + 1], 326)]
            c.line(path)
            if q > starts[i] + 1.3:
                c.packet(path, ease((q - starts[i] - 1.3) / 0.6))
        for i, x in enumerate(xs):
            age = q - starts[i]
            ready = age >= 1.3
            lift = 6 * math.sin((age - 1.3) * math.pi / 0.6) if 1.3 < age < 1.9 else 0
            module(
                c, x, 250 - lift, names[i], detail[i], ready, ease(age / 1.3) if age >= 0 else None
            )
            c.text(
                x + 15,
                435,
                "Ready" if ready else ("Checking..." if age >= 0 else "Waiting for dependency"),
                16,
                GREEN if ready else c.muted,
            )
        if q < 5.5:
            c.pill(395, 177, "Application is not ready yet", size=16)
        else:
            c.rect((401, 169, 724, 215), "#E2F4EE", 12)
            c.tick(426, 191, GREEN)
            c.text(450, 178, "All checks passed. Open app.", 17, GREEN, True)
    else:
        q = t - 16
        c.chrome(
            "PIPEDECK",
            "The proof is a real round trip.",
            3,
            "Open the app. Save a record. Read it back.",
            t,
        )
        # App is the user's outcome; infrastructure sits visibly behind it.
        c.card(65, 167, 450, 348)
        c.rect((66, 168, 514, 209), "#E9EDF7", 15)
        for i in range(3):
            c.circle(84 + i * 15, 188, 3, "#B2BED3")
        c.text(153, 178, "localhost / integration demo", 14, c.muted)
        c.text(91, 231, "Test your application", 26, bold=True)
        c.text(91, 275, "A small record, through the whole stack.", 16, c.muted)
        c.rect((91, 318, 370, 362), c.bg, 10, c.edge)
        prompt = "Hello, local integration"
        count = int(len(prompt) * ease(q / 1.1))
        c.text(105, 328, prompt[:count], 17)
        c.rect((384, 318, 488, 362), BLUE, 10)
        c.text(415, 328, "Save", 17, "#ffffff", True)
        if 1.1 < q < 1.9:
            x, y = 449, 351
            c.poly([(x, y), (x + 5, y + 20), (x + 10, y + 13), (x + 18, y + 13)], "#303746")
            if q > 1.5:
                c.circle(x, y, 18, None, BLUE, 2)
        ys = [167, 296, 425]
        labels = ["Frontend", "Backend", "SQLite"]
        for i, y in enumerate(ys):
            if i < 2:
                c.line([(848, y + 88), (848, ys[i + 1])])
        route = [(515, 340), (606, 340), (606, 211), (718, 211), (848, 211), (848, 469)]
        c.line(route)
        if q > 1.7:
            c.packet(route, ease((q - 1.7) / 2.3))
        returned = [(718, 469), (573, 469), (573, 470), (515, 470)]
        c.line(returned)
        if q > 4:
            c.packet(returned, ease((q - 4) / 1.1), GREEN)
        for i, y in enumerate(ys):
            active = q > 2.2 + i * 0.8
            c.card(718, y, 283, 88, "#ffffff")
            c.text(740, y + 14, labels[i], 20, bold=True)
            c.text(
                740,
                y + 49,
                ["Send request", "Handle and persist", "Store the record"][i],
                14,
                c.muted,
            )
            if active:
                c.tick(974, y + 32, GREEN)
        if q > 5.1:
            lift = 9 * (1 - spring(min((q - 5.1) / 0.8, 1.5)))
            c.rect((91, 393 + lift, 488, 479 + lift), "#E2F4EE", 12)
            c.text(108, 406 + lift, "Saved and read back", 17, GREEN, True)
            c.text(108, 441 + lift, "#001   Hello, local integration", 16)
            c.tick(458, 425 + lift, GREEN)
    return c.end()


if __name__ == "__main__":
    render(scene, Path(__file__).parent)
