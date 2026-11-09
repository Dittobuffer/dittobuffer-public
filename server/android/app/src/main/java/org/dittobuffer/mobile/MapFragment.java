package org.dittobuffer.mobile;

import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

/** Map tab: verifies the plain-HTTP vector-tile origin on 8081. */
public final class MapFragment extends Fragment {
    private TextView state;
    private TextView output;

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        View view = inflater.inflate(R.layout.fragment_map, container, false);
        state = view.findViewById(R.id.map_state);
        output = view.findViewById(R.id.map_output);

        view.findViewById(R.id.btn_map_health).setOnClickListener(v -> run(
                "http://127.0.0.1:8081/map/settings"));
        // A tile inside the bundled OSM extract (The Hague), so a 200 with
        // real road geometry proves the tile pipeline, not just the port.
        view.findViewById(R.id.btn_map_tile).setOnClickListener(v -> run(
                "http://127.0.0.1:8081/map/tiles/16/33542/21622"));
        view.findViewById(R.id.btn_map_game).setOnClickListener(v -> run(
                "http://127.0.0.1:8445/world/health"));
        return view;
    }

    @Override
    public void onResume() {
        super.onResume();
        Status.probe(requireContext(), status -> {
            if (!isAdded() || getView() == null) return;
            state.setText("Map origin 8081 " + (status.map ? "● listening" : "○ down")
                    + "    Game 8445 " + (status.game ? "●" : "○")
                    + "\nTiles are protobuf vector tiles used by the game client.");
        });
    }

    private void run(String url) {
        output.setText("GET " + url + " …");
        new Thread(() -> {
            String result = Status.fetch(url, 4000);
            if (!isAdded()) return;
            requireActivity().runOnUiThread(() -> output.setText(
                    "GET " + url + "\n\n" + (result.length() > 4000 ? result.substring(0, 4000) : result)));
        }, "map-probe").start();
    }
}
