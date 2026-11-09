// Copyright (C) Dittobuffer Contributors
// Copyright (C) chucny
// Copyright (C) gekkedavi
//
// This file is part of Dittobuffer. It is distributed under the GNU General
// Public License v3.0 (see LICENSE). The copyright notices in this file must
// be preserved in all copies, forks, and derivative works. Removing or
// altering these notices violates the license and copyright law.

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
    private MaterialButton battery;
    private MaterialButton start;
    private MaterialButton stop;

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

        start = view.findViewById(R.id.btn_start);
        stop = view.findViewById(R.id.btn_stop);
        MaterialButton launch = view.findViewById(R.id.btn_launch);

        start.setOnClickListener(v -> {
            ServerService.start(requireContext());
            state.setText("Starting Python server… first run unpacks the bootstrap");
        });
        stop.setOnClickListener(v -> {
            ServerService.stop(requireContext());
            ServerRuntime.markStopped();
            state.setText("Server is not running — press Start");
        });
        launch.setOnClickListener(v -> {
            if (!ClientKit.installed(requireContext())) {
                state.setText("Installing the bundled game client…");
                ClientKit.install(requireContext(), state::setText);
                return;
            }
            if (!ServerRuntime.isRunning()) {
                state.setText(ServerRuntime.isStarting()
                        ? "The server is starting — try Play again in a moment."
                        : "Start the server before launching the game.");
                return;
            }
            MainActivity.launchGame(requireContext());
        });
        view.findViewById(R.id.btn_install).setOnClickListener(v -> {
            state.setText("Installing the bundled family client…");
            ClientKit.install(requireContext(), state::setText);
        });
        battery = view.findViewById(R.id.btn_battery);
        battery.setOnClickListener(v -> MainActivity.requestBatteryExemption(requireContext()));
        error.setOnClickListener(v -> {
            error.setVisibility(View.GONE);
            ServerService.stop(requireContext());
            ServerRuntime.markStopped();
            state.setText("Restarting the local server…");
            handler.postDelayed(() -> ServerService.start(requireContext()), 1000);
        });
        view.findViewById(R.id.btn_home_admin).setOnClickListener(v -> {
            if (getActivity() instanceof MainActivity) {
                ((MainActivity) getActivity()).openTab(R.id.nav_admin);
            }
        });
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
                    : (ServerRuntime.isRunning() || ServerRuntime.isStarting()
                        ? "Starting the server… first run unpacks the payload"
                        : "Server is not running — press Start"));
            String uptime = ServerRuntime.uptime();
            ports.setText("Game 8445 " + dot(status.game)
                    + "   Admin 8080 " + dot(status.admin)
                    + "   Map 8081 " + dot(status.map)
                    + (uptime.isEmpty() ? "" : "   Up " + uptime));
            payload.setText(status.payload);
            // Never grey out Start for the whole unpack window: first run can
            // take many minutes, and a duplicate start is ignored by the
            // service anyway. Only a genuinely running server disables it.
            start.setEnabled(!ServerRuntime.isRunning());
            stop.setEnabled(ServerRuntime.isRunning() || ServerRuntime.isStarting());
            boolean exempt = MainActivity.isBatteryExempt(requireContext());
            battery.setText(exempt ? "Background running allowed" : "Allow background running");
            battery.setEnabled(!exempt);
            String lastError = ServerRuntime.lastError();
            if (lastError.isEmpty()) {
                error.setVisibility(View.GONE);
            } else {
                error.setVisibility(View.VISIBLE);
                error.setText("The server hit a problem. Tap here to restart it.\n"
                        + "Full details are in the Server tab.");
            }
        });
    }

    private String dot(boolean up) {
        return up ? "●" : "○";
    }
}
