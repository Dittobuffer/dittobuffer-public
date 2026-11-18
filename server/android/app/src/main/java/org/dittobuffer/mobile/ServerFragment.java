package org.dittobuffer.mobile;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.fragment.app.Fragment;

/** Server tab: live ports, restart/stop, tail of the on-device logs. */
public final class ServerFragment extends Fragment {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private TextView state;
    private TextView log;

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
        View view = inflater.inflate(R.layout.fragment_server, container, false);
        state = view.findViewById(R.id.server_state);
        log = view.findViewById(R.id.server_log);

        view.findViewById(R.id.btn_server_refresh).setOnClickListener(v -> refresh());
        view.findViewById(R.id.btn_server_restart).setOnClickListener(v -> {
            ServerService.stop(requireContext());
            ServerRuntime.markStopped();
            handler.postDelayed(() -> ServerService.start(requireContext()), 1200);
            state.setText("Restarting…");
        });
        view.findViewById(R.id.btn_server_stop).setOnClickListener(v -> {
            ServerService.stop(requireContext());
            ServerRuntime.markStopped();
            state.setText("Stopped");
        });
        view.findViewById(R.id.btn_server_open_admin).setOnClickListener(v -> {
            if (getActivity() instanceof MainActivity) {
                ((MainActivity) getActivity()).openTab(R.id.nav_admin);
            }
        });
        view.findViewById(R.id.btn_server_copy).setOnClickListener(v ->
                copyLog(Logs.tail(requireContext(), 250)));
        view.findViewById(R.id.btn_server_share).setOnClickListener(v -> {
            Intent send = new Intent(Intent.ACTION_SEND)
                    .setType("text/plain")
                    .putExtra(Intent.EXTRA_SUBJECT, "Dittobuffer server log")
                    .putExtra(Intent.EXTRA_TEXT, Logs.tail(requireContext(), 250));
            startActivity(Intent.createChooser(send, "Share server log"));
        });
        return view;
    }

    private void copyLog(String text) {
        ClipboardManager clipboard = (ClipboardManager)
                requireContext().getSystemService(Context.CLIPBOARD_SERVICE);
        clipboard.setPrimaryClip(ClipData.newPlainText("Dittobuffer log", text));
        Toast.makeText(requireContext(), "Log copied", Toast.LENGTH_SHORT).show();
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
            String uptime = ServerRuntime.uptime();
            state.setText("Game 8445 " + (status.game ? "● TLS" : "○ down")
                    + "    Admin 8080 " + (status.admin ? "●" : "○")
                    + "    Map 8081 " + (status.map ? "●" : "○")
                    + (ServerRuntime.isRunning() ? "    · running" + (uptime.isEmpty() ? "" : " " + uptime)
                        : ServerRuntime.isStarting() ? "    · starting…" : "    · service stopped"));
            log.setText(Logs.tail(requireContext(), 60));
        });
    }
}
