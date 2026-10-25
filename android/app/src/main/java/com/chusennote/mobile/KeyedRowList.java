package com.chusennote.mobile;

import android.view.View;
import android.widget.LinearLayout;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * A short list whose rows survive refreshes. Each row has a stable key and a
 * signature of everything it displays or acts on; a row's view is only rebuilt
 * when its signature changes, and the container is only re-populated when the
 * set or order of rows changes. A refresh that changes nothing touches no views.
 */
final class KeyedRowList {
    interface Factory {
        View build();
    }

    static final class Row {
        final String key;
        final String signature;
        final Factory factory;

        Row(String key, String signature, Factory factory) {
            this.key = key;
            this.signature = signature;
            this.factory = factory;
        }
    }

    private static final class Rendered {
        final String signature;
        final View view;

        Rendered(String signature, View view) {
            this.signature = signature;
            this.view = view;
        }
    }

    private final LinearLayout container;
    private Map<String, Rendered> rendered = new HashMap<>();

    KeyedRowList(LinearLayout container) {
        this.container = container;
    }

    LinearLayout container() {
        return container;
    }

    void render(List<Row> rows) {
        Map<String, Rendered> next = new HashMap<>();
        List<View> views = new ArrayList<>(rows.size());
        for (Row row : rows) {
            // A repeated key would hand the same view to two rows; give it its own.
            String key = row.key;
            for (int copy = 1; next.containsKey(key); copy++) {
                key = row.key + "#" + copy;
            }
            Rendered previous = rendered.get(key);
            View view = previous != null && previous.signature.equals(row.signature)
                    ? previous.view
                    : row.factory.build();
            next.put(key, new Rendered(row.signature, view));
            views.add(view);
        }
        rendered = next;
        if (!hasChildren(views)) {
            container.removeAllViews();
            for (View view : views) {
                container.addView(view);
            }
        }
    }

    private boolean hasChildren(List<View> views) {
        if (container.getChildCount() != views.size()) {
            return false;
        }
        for (int i = 0; i < views.size(); i++) {
            if (container.getChildAt(i) != views.get(i)) {
                return false;
            }
        }
        return true;
    }
}
