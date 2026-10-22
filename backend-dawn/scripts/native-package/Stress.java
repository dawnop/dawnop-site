import java.sql.*;

public class Stress {
    public static void main(String[] args) throws Exception {
        String path = args[0], mode = args[1], worker = args[2];
        int count = Integer.parseInt(args[3]), busy = 0, writes = 0, reads = 0, complete = 0, resets = 0, opens = 0;
        try (Connection c = DriverManager.getConnection("jdbc:sqlite::memory:"); Statement s = c.createStatement(); ResultSet r = s.executeQuery("SELECT sqlite_version(), sqlite_source_id()")) {
            if (!r.next() || !r.getString(1).equals("3.53.4")) throw new AssertionError("JDBC engine provenance");
            System.err.println("sqlite_version=" + r.getString(1) + " sqlite_source_id=" + r.getString(2));
        }
        for (int i = 0; i < count; ++i) {
            try (Connection c = DriverManager.getConnection("jdbc:sqlite:" + path); Statement s = c.createStatement()) {
                ++opens;
                s.execute("PRAGMA busy_timeout=30");
                s.execute("PRAGMA wal_autocheckpoint=0");
                try {
                    if (mode.equals("write")) {
                        s.execute("BEGIN IMMEDIATE");
                        try (PreparedStatement p = c.prepareStatement("INSERT INTO ledger VALUES(?, zeroblob(4096))")) {
                            p.setString(1, worker + "-" + i);
                            if (p.executeUpdate() != 1) throw new AssertionError("one write");
                        }
                        s.execute("COMMIT");
                        ++writes;
                    } else if (mode.equals("read")) {
                        try (ResultSet r = s.executeQuery("SELECT count(*) FROM ledger WHERE length(payload)!=4096")) {
                            if (!r.next() || r.getInt(1) != 0) throw new AssertionError("read invariant");
                            ++reads;
                        }
                    } else if (mode.equals("verify")) {
                        try (ResultSet r = s.executeQuery("PRAGMA integrity_check")) {
                            if (!r.next() || !r.getString(1).equals("ok")) throw new AssertionError("integrity");
                        }
                        try (ResultSet r = s.executeQuery("SELECT count(*) FROM ledger")) {
                            if (!r.next() || r.getInt(1) != Integer.parseInt(args[4])) throw new AssertionError("committed row count");
                        }
                    } else {
                        for (int j = 0; j < 3; ++j) {
                            try (ResultSet r = s.executeQuery("PRAGMA wal_checkpoint(" + (j == 2 ? "TRUNCATE" : "FULL") + ")")) {
                                if (!r.next()) throw new AssertionError("checkpoint row");
                                if (r.getInt(1) != 0) ++busy;
                                else if (j < 2 && r.getInt(2) == r.getInt(3)) ++complete;
                                else if (j == 2 && r.getInt(2) == 0 && r.getInt(3) == 0) ++resets;
                            }
                        }
                    }
                } catch (SQLException e) {
                    if (e.getErrorCode() != 5 && e.getErrorCode() != 6) throw e;
                    ++busy;
                    if (mode.equals("write")) --i;
                }
            }
        }
        System.out.printf("{\"engine\":\"3.53.4\",\"worker\":\"%s\",\"opens\":%d,\"writes\":%d,\"reads\":%d,\"busy\":%d,\"complete\":%d,\"resets\":%d}%n", worker, opens, writes, reads, busy, complete, resets);
    }
}
