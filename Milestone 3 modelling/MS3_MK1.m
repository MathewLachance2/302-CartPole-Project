%% MECH 302 Milestone 3 | Pendulum properties
% Run with PendulumV2.stl beside this script. The Command Window shows
% report values (3 significant figures). MS3_parameters.csv keeps the
% unrounded coefficients for subsequent modelling, one row per configuration.
% Source: M3 template pp. 2-4 and 6. SI units except where explicitly marked.

%% Inputs
scriptDir = fileparts(mfilename('fullpath'));
bodyMeshFile = fullfile(scriptDir, 'PendulumV2.stl');
parametersFile = fullfile(scriptDir, 'MS3_parameters.csv');
gravity = 9.81;                         % m/s^2
bodyMass = 29.0e-3;                      % kg, printed body alone
% Pocket order: left, centre, right. All three nuts stay installed.
nutMass = [4.6, 4.6, 4.6] * 1e-3;       % kg
boltMass = [16.3, 16.3, 16.3] * 1e-3;   % kg
pocketRadius = [112, 112, 112] * 1e-3;  % m, from shaft to fastener centre
pocketAngle = [-9.5, 0, 9.5];           % deg, from downward centreline
installedBolts = [0 1 0; 1 0 1; 1 1 1];
% Minimum required settings. Actual hardware maxima are unspecified.
minAccel_steps_s2 = 100000;
minCruiseSpeed_steps_s = 6000;

%% Printed body: calculate once about the shaft (STL Z-axis at X=Y=0)
% Assume effective material density is uniform in the printed body. The
% flat-plate integral uses areal density proportional to local thickness.
% This accounts for the thinner sections, blind pockets and through-holes.
body = stlread(bodyMeshFile);
points = body.Points * 1e-3;             % mesh coordinates: mm -> m
faces = body.ConnectivityList;
p = points(faces(:,1), :);
q = points(faces(:,2), :);
r = points(faces(:,3), :);

% This particular STL consists of horizontal surfaces and vertical walls.
% Looking down the shaft, each horizontal triangle covers a signed area:
% a top face contributes +area*height and a bottom face -area*height.
% Their sum integrates the material thickness above each point of the plate.
signedArea = ((q(:,1)-p(:,1)).*(r(:,2)-p(:,2)) ...
            - (q(:,2)-p(:,2)).*(r(:,1)-p(:,1))) / 2;
zRange = max([p(:,3), q(:,3), r(:,3)], [], 2) ...
       - min([p(:,3), q(:,3), r(:,3)], [], 2);
assert(all(abs(signedArea) < 1e-15 | zRange < 1e-9), ...
    'This integration method requires horizontal faces and vertical walls.');
signedVolume = signedArea .* p(:,3);
bodyVolume = sum(signedVolume);
assert(bodyVolume > 0, 'Expected an outward-oriented, closed body mesh.');
bodyDensity = bodyMass / bodyVolume;
triangleCentresXY = (p(:,1:2) + q(:,1:2) + r(:,1:2)) / 3;
bodyCentreXY = sum(signedVolume .* triangleCentresXY, 1) / bodyVolume;

% I_body = integral (x^2 + y^2) dm about the pivot. Use the exact average
% of x^2 + y^2 over each projected triangle, including its own extent.
x = [p(:,1), q(:,1), r(:,1)];
y = [p(:,2), q(:,2), r(:,2)];
meanRadiusSquared = (sum(x.^2 + y.^2,2) + sum(x,2).^2 + sum(y,2).^2) / 12;
bodyInertia = bodyDensity * sum(signedVolume .* meanRadiusSquared);

%% Assemble the body, nuts and selected bolts for each configuration
% Treat the fasteners as point masses at the pocket centres: I = m*r^2.
% Neglect their small inertia about their own axes, the shaft and the rotor.
pocketXY = [pocketRadius(:).*sind(pocketAngle(:)), ...
           -pocketRadius(:).*cosd(pocketAngle(:))];
boltCount = sum(installedBolts,2);
numConfigs = numel(boltCount);
M = zeros(numConfigs,1);
d = zeros(numConfigs,1);
Ip = zeros(numConfigs,1);
for k = 1:numConfigs
    fastenerMass = nutMass + installedBolts(k,:) .* boltMass;
    M(k) = bodyMass + sum(fastenerMass);
    centreXY = (bodyMass * bodyCentreXY + fastenerMass * pocketXY) / M(k);
    d(k) = norm(centreXY);               % distance to the combined centre of mass
    Ip(k) = bodyInertia + sum(fastenerMass .* pocketRadius.^2);
end
Leq = Ip ./ (M .* d);
omegaN = sqrt(gravity ./ Leq);            % undamped small-angle angular frequency

%% Display report values and export parameters for later simulation
fprintf('\nRequired minimum acceleration: %d steps/s^2\n', minAccel_steps_s2);
fprintf('Required minimum cruise speed: %d steps/s\n', minCruiseSpeed_steps_s);
fprintf('Printed body alone: d = %#.3g m, Ip = %#.3g kg*m^2\n', ...
    norm(bodyCentreXY), bodyInertia);
fprintf('\nBolts     M [kg]      d [m]      Ip [kg*m^2]      Leq [m]      wn [rad/s]\n');
for k = 1:numConfigs
    fprintf('%5d %#10.3g %#10.3g %#16.3g %#12.3g %#15.3g\n', ...
        boltCount(k), M(k), d(k), Ip(k), Leq(k), omegaN(k));
end
parameters = table(boltCount, M, d, Ip, Leq, omegaN, ...
    'VariableNames', {'bolt_count','mass_kg','d_m','Ip_kg_m2','Leq_m','omega_n_rad_s'});
writetable(parameters, parametersFile);
fprintf('\nSaved %s\n', parametersFile);
