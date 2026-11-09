package org.dittobuffer.mobile;

import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

import com.google.android.material.button.MaterialButton;

/** Home: server state, start/stop, launch the game client, payload status. */
public final class HomeFragment extends Fragment {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView state;
    private TextView ports;
    private TextView payload;
    private TextView error;

    private final Runnable poll = new Runnable() {
        @Override public void run() {
            refresh();
            handler.postDelayed(this, 2500);
        }
    };

    @Nullable
    @Override
    public View onCreateView(@NonNull LayoutInflater inflater, @Nullable ViewGroup container,
                             @Nullable Bundle savedInstanceState) {
        View view = inflater.inflate(R.layout.fragment_home, container, false);
        state = view.findViewById(R.id.home_state);
        ports = view.findViewById(R.id.home_ports);
        payload = view.findViewById(R.id.home_payload);
        error = view.findViewById(R.id.home_error);

        MaterialButton start = view.findViewById(R.id.btn_start);
        MaterialButton stop = view.findViewById(R.id.btn_stop);
        MaterialButton launch = view.findViewById(R.id.btn_launch);

        start.setOnClickListener(v -> {
            ServerService.start(requireContext());
            state.setText("Starting Python server… first run unpacks the bootstrap");
        });
        stop.setOnClickListener(v -> {
            ServerService.stop(requireContext());
            ServerRuntime.markStopped();
            state.setText("Server stopped");
        });
        launch.setOnClickListener(v -> {
            if (!ClientKit.installed(requireContext())) {
                state.setText("Installing the bundled game client…");
                ClientKit.install(requireContext(), state::setText);
                return;
            }
            if (!ServerRuntime.isRunning()) {
                state.setText("Start the server before launching the game.");
                return;
            }
            MainActivity.launchGame(requireContext());
        });
        view.findViewById(R.id.btn_install).setOnClickListener(v -> {
            state.setText("Installing the bundled family client…");
            ClientKit.install(requireContext(), state::setText);
        });
        view.findViewById(R.id.btn_battery).setOnClickListener(
                v -> MainActivity.requestBatteryExemption(requireContext()));
        return view;
    }

    @Override
    public void onResume() {
        super.onResume();
        handler.post(poll);
    }

    @Override
    public void onPause() {
        handler.removeCallbacks(poll);
        super.onPause();
    }

    private void refresh() {
        if (!isAdded()) return;
        Status.probe(requireContext(), status -> {
            if (!isAdded() || getView() == null) return;
            state.setText(status.game
                    ? "Server running — ready to play"
                    : (ServerRuntime.isRunning() ? "Starting…" : "Server stopped"));
            ports.setText("Game 8445 " + dot(status.game)
                    + "   Admin 8080 " + dot(status.admin)
                    + "   Map 8081 " + dot(status.map));
            payload.setText(status.payload);
            String lastError = ServerRuntime.lastError();
            if (lastError.isEmpty()) {
                error.setVisibility(View.GONE);
            } else {
                error.setVisibility(View.VISIBLE);
                error.setText(lastError.length() > 1200 ? lastError.substring(0, 1200) : lastError);
            }
        });
    }

    private String dot(boolean up) {
        return up ? "●" : "○";
    }
}
