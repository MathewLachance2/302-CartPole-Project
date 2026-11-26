function result = MS3_simulate_case(config, distanceMm, damping)
% Simulate one open-loop cart displacement and five seconds of free swing.
% config is one row of MS3_parameters.csv. Positive x and theta point right.
% damping is the assumed rotational coefficient c [N*m*s/rad].
validateattributes(distanceMm, {'double'}, {'scalar','real','finite'});
validateattributes(damping, {'double'}, {'scalar','real','finite','nonnegative'});
gravity = 9.81;
% M2 cart settings: 100000 steps/s^2, 7000 steps/s and 40 steps/mm.
accel = 2500;                      % mm/s^2
maxSpeed = 175;                    % mm/s
freeTime = 5;                      % s
mass = config.mass_kg;
arm = config.d_m;
inertia = config.Ip_kg_m2;
validateattributes([mass arm inertia],{'double'},{'real','finite','positive','numel',3});

% A short move accelerates then brakes. A longer move also cruises.
travel = abs(distanceMm);
direction = sign(distanceMm);
rampTime = min(maxSpeed/accel, sqrt(travel/accel));
speed = accel*rampTime;
cruiseTime = max(0,(travel - speed*rampTime)/maxSpeed);
stopTime = 2*rampTime + cruiseTime;
if eps(stopTime + freeTime) >= 0.01
    error('MS3:TimeResolution','This displacement exceeds the numerical time resolution of the simulation.');
end
durations = [rampTime, cruiseTime, rampTime, freeTime];
accelerations = direction*[accel, 0, -accel, 0];

% Linearized template equation: Ip*theta'' + c*theta' + M*g*d*theta = -M*d*x''.
% Start at rest. Across each constant-acceleration phase the matrix
% exponential advances the exact linear dynamics, including at the cart stop.
% The third state stays equal to one to represent the constant forcing.
state = [0;0;1];
t = 0; x = 0; angle = 0; velocity = 0;
for phase = 1:numel(durations)
    duration = durations(phase);
    if duration == 0, continue; end
    a = accelerations(phase);
    dynamics = [0, 1, 0; ...
        -mass*gravity*arm/inertia, -damping/inertia, -mass*arm/inertia*a/1000; ...
        0, 0, 0];
    % At least 100 samples/s for normal lab runs. Limit display samples on
    % long cruises. Always retain phase endpoints and the full free response.
    count = min(20000,max(1,ceil(duration*100)));
    localTime = (1:count)' * (duration/count);
    transition = expm(dynamics*(duration/count));
    phaseStart = state;
    phaseAngle = zeros(count,1);
    for j = 1:count
        state = transition*state;
        phaseAngle(j) = state(1);
    end
    state = expm(dynamics*duration)*phaseStart;
    phaseAngle(end) = state(1);
    phaseX = x(end) + velocity*localTime + 0.5*a*localTime.^2;
    velocity = velocity + a*duration;
    if phase == 3
        phaseX(end) = distanceMm;
        velocity = 0;
    end
    t = [t; t(end)+localTime]; %#ok<AGROW>
    x = [x; phaseX]; %#ok<AGROW>
    angle = [angle; phaseAngle]; %#ok<AGROW>
end
result = table(t,x,rad2deg(angle),'VariableNames',{'time_s','cart_mm','angle_deg'});
result.Properties.UserData = struct('stopTime_s',stopTime, ...
    'speedLimit_mm_s',maxSpeed,'accel_mm_s2',accel);
% Store both the coefficients used by the model and those identified from
% its free swing. The signed envelope convention is A(t) = A0*exp(lambda*t).
b = damping/inertia;
lambda = -b/2;
oscillatory = b/2 < sqrt(mass*gravity*arm/inertia);
if ~oscillatory, lambda = NaN; end  % no oscillatory amplitude envelope
free = t >= stopTime;
[fit,peakCount,fitNote] = fitFreeSwing(t(free)-stopTime,angle(free),inertia,oscillatory);
result.Properties.UserData.damping = table([b;damping;lambda],fit, ...
    'VariableNames',{'Model','SwingFit'},'RowNames',{'b','c','lambda'});
result.Properties.UserData.peakCount = peakCount;
result.Properties.UserData.fitNote = fitNote;
end

function [fit,n,note] = fitFreeSwing(t,theta,inertia,oscillatory)
% Estimate lambda from the slope of log(|angle at peak|) versus time.
% Both half-cycles carry decay information. Exclude both segment endpoints.
fit = nan(3,1); n = 0;
if ~oscillatory
    note = 'No oscillatory envelope at this damping.';
    return
end
a = abs(theta);
if max(a) == 0
    note = 'No swing to fit.';
    return
end
idx = find(a(2:end-1)>a(1:end-2) & a(2:end-1)>=a(3:end))+1;
idx = idx(a(idx)>max(a)*1e-10);
n = numel(idx);
if n < 3
    note = 'Too few resolved peaks to fit the decay.';
    return
end
% The free-response samples are equally spaced. A parabola through the
% three samples surrounding each maximum refines its time and magnitude.
left = a(idx-1); middle = a(idx); right = a(idx+1);
shift = 0.5*(left-right)./(left-2*middle+right);
peakTime = t(idx) + shift.*(t(idx+1)-t(idx-1))/2;
logAmp = log(middle - 0.25*(left-right).*shift);
coeff = polyfit(peakTime,logAmp,1);
lambda = coeff(1);
% Sub-sample peak interpolation can give a tiny slope for a constant
% envelope. Report zero when the fit cannot resolve a trend above scatter.
residual = logAmp - polyval(coeff,peakTime);
slopeError = norm(residual) / sqrt((n-2)*sum((peakTime-mean(peakTime)).^2));
if abs(lambda) <= max(3*slopeError,64*eps/max(peakTime(end)-peakTime(1),eps))
    lambda = 0;
end
b = -2*lambda;
fit = [b;inertia*b;lambda];
note = sprintf('Fit from %d peaks after the cart stopped.',n);
end
