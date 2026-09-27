// generate_ae.jsx  (v2: speaker-following reframe)
//
// Builds 1080x1920 vertical comps from:
//   clips/<id>.mp4               - extracted horizontal clip
//   camera_tracks/<id>.json      - speaker-aware camera path (analyze_speakers.py)
//   edit_events/<id>.json        - captions, punch-ins, keyword emphasis
//
// Camera model:
//   VIDEO layer Position is pinned to the comp centre; its Anchor Point is
//   keyframed to the camera target in source pixels, so the anchor is always
//   what sits in the middle of the vertical frame. Scale = fill-height * shot
//   zoom (hold keys at cuts). Punch-ins live on a "Punch" slider multiplied in
//   by expression, so they never fight the camera keys. An Anchor Point
//   expression clamps the view so the frame edges can never show.
//
// Run: After Effects > File > Scripts > Run Script File... > this file.
// The previous centre-crop version is kept as generate_ae_v1_center.jsx.

(function () {

    // --------------------------------------------------
    // SETTINGS
    // --------------------------------------------------

    var PROJECT_ROOT = "D:/AI-CLIP-GENERATOR/";
    var CLIPS_FOLDER = PROJECT_ROOT + "clips/";
    var EVENTS_FOLDER = PROJECT_ROOT + "edit_events/";
    var TRACKS_FOLDER = PROJECT_ROOT + "camera_tracks/";

    var CLIP_NUMBERS = [1];          // e.g. [1,2,3,4,5,6,7,8,9,10]

    var WIDTH = 1080;
    var HEIGHT = 1920;

    var CAPTION_Y = 0.72;            // fraction of comp height
    var KEYWORD_Y = 0.60;

    var project = app.project;
    if (!project) {
        app.newProject();
        project = app.project;
    }

    app.beginUndoGroup("AI Clip Generator - Reframe");

    // --------------------------------------------------
    // HELPERS
    // --------------------------------------------------

    function importFootage(path) {
        var file = new File(path);
        if (!file.exists) {
            alert("File not found:\n" + path);
            return null;
        }
        var importOptions = new ImportOptions(file);
        if (!importOptions.canImportAs(ImportAsType.FOOTAGE)) {
            alert("Cannot import:\n" + path);
            return null;
        }
        importOptions.importAs = ImportAsType.FOOTAGE;
        return project.importFile(importOptions);
    }

    function readJSON(path, required) {
        var file = new File(path);
        if (!file.exists) {
            if (required) {
                alert("JSON file not found:\n" + path);
            }
            return null;
        }
        file.encoding = "UTF-8";
        file.open("r");
        var contents = file.read();
        file.close();
        try {
            return JSON.parse(contents);
        } catch (e) {
            alert("Invalid JSON:\n" + path + "\n\n" + e.toString());
            return null;
        }
    }

    function addText(comp, text, time, duration, fontSize, color, yFrac) {
        var layer = comp.layers.addBoxText([WIDTH * 0.86, 420], text);

        layer.startTime = 0;
        layer.inPoint = time;
        layer.outPoint = Math.min(time + duration, comp.duration);

        var textProperty = layer.property("Source Text");
        var doc = textProperty.value;
        doc.fontSize = fontSize;
        doc.fillColor = color;
        doc.applyFill = true;
        doc.applyStroke = true;
        doc.strokeColor = [0, 0, 0];
        doc.strokeWidth = 8;
        doc.strokeOverFill = false;
        doc.justification = ParagraphJustification.CENTER_JUSTIFY;
        textProperty.setValue(doc);

        layer.property("Transform").property("Position")
             .setValue([WIDTH / 2, HEIGHT * yFrac]);

        return layer;
    }

    // Punch-ins drive the "Punch" slider (percent), never Scale directly.
    function addPunch(punch, time, amount, layerEnd) {
        var peak = 100 * amount;
        var t0 = Math.max(0, time);
        var t1 = Math.min(t0 + 0.15, layerEnd);
        var t2 = Math.min(t0 + 0.45, layerEnd);
        if (t1 <= t0 || t2 <= t1) {
            return;
        }
        punch.setValueAtTime(t0, 100);
        punch.setValueAtTime(t1, peak);
        punch.setValueAtTime(t2, 100);
    }

    function easeAllKeys(prop) {
        var ease = new KeyframeEase(0, 60);
        for (var k = 1; k <= prop.numKeys; k++) {
            prop.setTemporalEaseAtKey(k, [ease], [ease]);
        }
    }

    // --------------------------------------------------
    // CAMERA
    // --------------------------------------------------

    function applyCamera(videoLayer, track, baseScale, footage) {
        var anchor = videoLayer.property("Transform").property("Anchor Point");
        var scale = videoLayer.property("Transform").property("Scale");

        var keys = (track && track.camera_keyframes) ? track.camera_keyframes : [];

        if (keys.length === 0) {
            // Fallback: static centre (only when no track data exists)
            anchor.setValue([footage.width / 2, footage.height / 2]);
            scale.setValue([baseScale, baseScale]);
            return 0;
        }

        var times = [];
        var anchorValues = [];
        var scaleTimes = [];
        var scaleValues = [];
        var lastZoom = null;

        for (var i = 0; i < keys.length; i++) {
            var k = keys[i];
            times.push(Number(k.t));
            anchorValues.push([Number(k.x), Number(k.y)]);

            var z = Number(k.zoom || 1);
            if (lastZoom === null || z !== lastZoom || k.cut_in) {
                scaleTimes.push(Number(k.t));
                scaleValues.push([baseScale * z, baseScale * z]);
                lastZoom = z;
            }
        }

        anchor.setValuesAtTimes(times, anchorValues);
        scale.setValuesAtTimes(scaleTimes, scaleValues);

        // Anchor: linear between dense keys, HOLD across hard cuts.
        for (var a = 1; a <= anchor.numKeys; a++) {
            var holdOut = keys[a - 1] && keys[a - 1].hold_out;
            anchor.setInterpolationTypeAtKey(
                a,
                KeyframeInterpolationType.LINEAR,
                holdOut ? KeyframeInterpolationType.HOLD : KeyframeInterpolationType.LINEAR
            );
            try {
                anchor.setSpatialAutoBezierAtKey(a, false);
                anchor.setSpatialContinuousAtKey(a, false);
                anchor.setSpatialTangentsAtKey(a, [0, 0, 0], [0, 0, 0]);
            } catch (e) { /* spatial tangents are cosmetic here */ }
        }

        // Zoom changes only happen at cuts.
        for (var s = 1; s <= scale.numKeys; s++) {
            scale.setInterpolationTypeAtKey(
                s,
                KeyframeInterpolationType.HOLD,
                KeyframeInterpolationType.HOLD
            );
        }

        return keys.length;
    }

    // --------------------------------------------------
    // PROCESS CLIP
    // --------------------------------------------------

    function processClip(clipNumber) {

        var id = "clip_" + ("0" + clipNumber).slice(-2);

        var events = readJSON(EVENTS_FOLDER + id + ".json", true);
        if (!events) {
            return null;
        }

        var track = readJSON(TRACKS_FOLDER + id + ".json", false);

        var footage = importFootage(CLIPS_FOLDER + id + ".mp4");
        if (!footage) {
            return null;
        }

        var duration = footage.duration;
        var fps = footage.frameRate || 25;

        var comp = project.items.addComp(
            id + "_VERTICAL", WIDTH, HEIGHT, 1, duration, fps
        );

        // ---------------- VIDEO + CAMERA ----------------

        var videoLayer = comp.layers.add(footage);
        videoLayer.name = "VIDEO";
        videoLayer.inPoint = 0;
        videoLayer.outPoint = duration;

        // Fill the vertical frame's height; horizontal position comes from the camera.
        var baseScale = Math.max(WIDTH / footage.width, HEIGHT / footage.height) * 100;

        videoLayer.property("Transform").property("Position")
                  .setValue([WIDTH / 2, HEIGHT / 2]);

        var punchFx = videoLayer.property("ADBE Effect Parade")
                                .addProperty("ADBE Slider Control");
        punchFx.name = "Punch";
        var punch = punchFx.property(1);
        punch.setValue(100);

        var nKeys = applyCamera(videoLayer, track, baseScale, footage);

        videoLayer.property("Transform").property("Scale").expression =
            'var p = effect("Punch")(1) / 100;\n' +
            '[value[0] * p, value[1] * p]';

        videoLayer.property("Transform").property("Anchor Point").expression =
            '// keep the view inside the source frame at any zoom\n' +
            'var s = transform.scale[0] / 100;\n' +
            'var hw = thisComp.width / 2 / s;\n' +
            'var hh = thisComp.height / 2 / s;\n' +
            '[clamp(value[0], hw, width - hw), clamp(value[1], hh, height - hh)]';

        // ---------------- EVENTS ----------------

        var eventList = events.events || [];

        for (var i = 0; i < eventList.length; i++) {

            var event = eventList[i];
            var time = Number(event.time);

            if (event.type === "caption") {
                var start = Number(event.start);
                var end = Number(event.end);
                if (!(end > start) || !event.text) {
                    continue;
                }
                var caption = addText(
                    comp, event.text, start, end - start,
                    64, [1, 1, 1], CAPTION_Y
                );
                caption.name = "CAPTION_" + i;
            }

            if (event.type === "hook" || event.type === "speaker_change") {
                addPunch(punch, time, Number(event.scale || 1.05), duration);
            }

            if (event.type === "keyword_emphasis") {
                var words = event.words || [];
                if (words.length > 0) {
                    var emphasis = addText(
                        comp, words.join(" ").toUpperCase(), time, 0.7,
                        96, [1, 0.85, 0.15], KEYWORD_Y
                    );
                    emphasis.name = "KEYWORD_" + i;
                }
            }

            if (event.type === "ending") {
                punch.setValueAtTime(Math.min(time, duration), 100);
            }
        }

        easeAllKeys(punch);

        comp.openInViewer();

        return {
            comp: comp,
            cameraKeys: nKeys
        };
    }

    // --------------------------------------------------
    // RUN
    // --------------------------------------------------

    var report = [];

    for (var c = 0; c < CLIP_NUMBERS.length; c++) {
        var res = processClip(CLIP_NUMBERS[c]);
        if (res) {
            report.push(
                res.comp.name + ": " +
                (res.cameraKeys > 0
                    ? res.cameraKeys + " camera keyframes"
                    : "NO camera track - centred fallback")
            );
        }
    }

    app.endUndoGroup();

    alert(
        "AI Clip Generator finished.\n\n" +
        report.length + " vertical composition(s) created:\n\n" +
        report.join("\n")
    );

})();
