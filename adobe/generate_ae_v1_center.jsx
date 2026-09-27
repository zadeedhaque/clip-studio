(function () {

    app.beginUndoGroup("AI Clip Generator");

    var project = app.project;

    if (!project) {
        app.newProject();
        project = app.project;
    }

    // --------------------------------------------------
    // SETTINGS
    // --------------------------------------------------

    var PROJECT_ROOT = "D:/AI-CLIP-GENERATOR/";
    var CLIPS_FOLDER = PROJECT_ROOT + "clips/";
    var EVENTS_FOLDER = PROJECT_ROOT + "edit_events/";

    var WIDTH = 1080;
    var HEIGHT = 1920;
    var FPS = 30;

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

    function readJSON(path) {
        var file = new File(path);

        if (!file.exists) {
            alert("JSON file not found:\n" + path);
            return null;
        }

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

    function addText(comp, text, time, duration) {

        var layer = comp.layers.addText(text);

        layer.startTime = time;
        layer.inPoint = time;
        layer.outPoint = Math.min(
            time + duration,
            comp.duration
        );

        var textProperty =
            layer.property("Source Text");

        var textDocument =
            textProperty.value;

        textDocument.fontSize = 72;
        textDocument.fillColor = [1, 1, 1];
        textDocument.applyFill = true;
        textDocument.applyStroke = true;
        textDocument.strokeColor = [0, 0, 0];
        textDocument.strokeWidth = 8;
        textDocument.justification =
            ParagraphJustification.CENTER_JUSTIFY;

        textProperty.setValue(textDocument);

        var position =
            layer.property("Transform")
                 .property("Position");

        position.setValue([
            WIDTH / 2,
            HEIGHT * 0.78
        ]);

        return layer;
    }

    function addZoomKeyframes(layer, time, scale) {

        var transform =
            layer.property("Transform");

        var scaleProperty =
            transform.property("Scale");

        var baseScale = scaleProperty.value;

        scaleProperty.setValueAtTime(
            time,
            baseScale
        );

        scaleProperty.setValueAtTime(
            Math.min(time + 0.15, layer.outPoint),
            [
                baseScale[0] * scale,
                baseScale[1] * scale
            ]
        );

        scaleProperty.setValueAtTime(
            Math.min(time + 0.45, layer.outPoint),
            baseScale
        );
    }

    // --------------------------------------------------
    // PROCESS CLIP
    // --------------------------------------------------

    function processClip(clipNumber) {

        var id =
            "clip_" +
            ("0" + clipNumber).slice(-2);

        var clipPath =
            CLIPS_FOLDER + id + ".mp4";

        var eventPath =
            EVENTS_FOLDER + id + ".json";

        var events =
            readJSON(eventPath);

        if (!events) {
            return;
        }

        var footage =
            importFootage(clipPath);

        if (!footage) {
            return;
        }

        var duration =
            footage.duration;

        var comp =
            project.items.addComp(
                id + "_VERTICAL",
                WIDTH,
                HEIGHT,
                1,
                duration,
                FPS
            );

        // --------------------------------------------------
        // VIDEO
        // --------------------------------------------------

        var videoLayer =
            comp.layers.add(footage);

        videoLayer.name =
            "VIDEO";

        videoLayer.inPoint = 0;
        videoLayer.outPoint = duration;

        // Fill vertical frame
        var videoWidth =
            footage.width;

        var videoHeight =
            footage.height;

        var scaleX =
            WIDTH / videoWidth;

        var scaleY =
            HEIGHT / videoHeight;

        var scale =
            Math.max(scaleX, scaleY) * 100;

        videoLayer
            .property("Transform")
            .property("Scale")
            .setValue([
                scale,
                scale
            ]);

        videoLayer
            .property("Transform")
            .property("Position")
            .setValue([
                WIDTH / 2,
                HEIGHT / 2
            ]);

        // --------------------------------------------------
        // EVENTS
        // --------------------------------------------------

        var eventList =
            events.events || [];

        for (var i = 0; i < eventList.length; i++) {

            var event =
                eventList[i];

            var time =
                Number(event.time);

            if (event.type === "caption") {

                var caption =
                    addText(
                        comp,
                        event.text,
                        time,
                        Number(event.end) - time
                    );

                caption.name =
                    "CAPTION_" + i;
            }

            if (
                event.type === "hook" ||
                event.type === "speaker_change"
            ) {

                addZoomKeyframes(
                    videoLayer,
                    time,
                    Number(event.scale || 1.05)
                );
            }

            if (event.type === "keyword_emphasis") {

                var words =
                    event.words || [];

                if (words.length > 0) {

                    var emphasis =
                        addText(
                            comp,
                            words.join(" "),
                            time,
                            0.7
                        );

                    emphasis.name =
                        "KEYWORD_" + i;

                    var emphasisText =
                        emphasis
                            .property("Source Text");

                    var document =
                        emphasisText.value;

                    document.fontSize = 96;
                    document.fillColor =
                        [1, 0.85, 0.15];

                    emphasisText.setValue(
                        document
                    );
                }
            }

            if (event.type === "ending") {

                videoLayer
                    .property("Transform")
                    .property("Scale")
                    .setValueAtTime(
                        time,
                        [100, 100]
                    );
            }
        }

        return comp;
    }

    // --------------------------------------------------
    // PROCESS ALL 10 CLIPS
    // --------------------------------------------------

processClip(1);

    app.endUndoGroup();

    alert(
        "AI Clip Generator finished.\n\n" +
        "10 vertical compositions created."
    );

})();