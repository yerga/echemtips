"""Illustrative electrochemical response, not a fitted transport/kinetics solver.

Units are V, s and nA. IUPAC polarity is used internally. State is advanced by
sample time, so potential holds produce transients rather than random spikes.
"""
import math


class SimulatedCell:
    """A wet cell with contact charging, redox sweep peaks and step relaxation."""
    def __init__(self):
        self.mode = 'contact'
        self.rate = .25
        self.last_e = None
        self.direction = 1
        self.wet = False
        self.contact_t = 0.
        self.contact_sign = 1.
        self.step_t = 0.
        self.step_delta = 0.

    def current(self, t, e, wet, activity=1.):
        """Return noiseless IUPAC current; caller adds low-amplitude measurement noise."""
        if wet and not self.wet:
            self.contact_t = t
            self.contact_sign = -1. if e < 0 else 1.
        self.wet = wet
        if self.last_e is not None:
            delta = e-self.last_e
            if abs(delta)>1e-8:
                self.direction = 1 if delta>0 else -1
                if self.mode != 'sweep':
                    self.step_delta = delta
                    self.step_t = t
        self.last_e = e
        if not wet: return 0.
        charging = self.contact_sign * .08 * math.exp(-max(0,t-self.contact_t)/.12)
        if self.mode == 'sweep':
            # Diffusion-like sqrt(scan-rate) peak scaling, separated branches.
            peak_e = .18 if self.direction>0 else .04
            peak = self.direction * .16 * math.sqrt(max(.001,self.rate)/.25) * math.exp(-.5*((e-peak_e)/.075)**2)
            return charging + activity*(peak + .008*self.direction*self.rate + .002*(e-.1))
        age = max(0,t-self.step_t)
        step = self.step_delta * (.16*math.exp(-age/.06) + .035/math.sqrt(1+age/.025))
        return charging + activity*(.012 + .008*math.tanh((e-.1)*5) + step)
