function fig = MS3_Simulator
% Run MS3_Simulator to explore open-loop cart moves and pendulum free decay.
% Requires MS3_simulate_case.m and MS3_parameters.csv beside this file.
folder = fileparts(mfilename('fullpath'));
parameterFile = fullfile(folder,'MS3_parameters.csv');
if ~isfile(parameterFile)
    error('MS3:MissingParameters','Run MS3_MK1.m first to generate MS3_parameters.csv.');
end
parameters = readtable(parameterFile);
required = {'bolt_count','mass_kg','d_m','Ip_kg_m2'};
if ~all(ismember(required,parameters.Properties.VariableNames)) || ...
        ~isequal(sort(parameters.bolt_count(:)),[1;2;3])
    error('MS3:Parameters','Regenerate MS3_parameters.csv with MS3_MK1.m.');
end
defaultDistance = 50;               % mm from the starting position
defaultDamping = 0.000131;          % mean of -2*Ip*lambda across 63 measured M3 trials

fig = uifigure('Name','MECH 302 | Cart–pendulum simulator', ...
    'Position',[100 100 1060 735],'Visible','off');
layout = uigridlayout(fig,[1 2]);
layout.ColumnWidth = {336,'1x'};
layout.Padding = [14 14 14 14];
layout.ColumnSpacing = 16;
sidebar = uigridlayout(layout,[3 1]);
sidebar.RowHeight = {405,208,'1x'}; sidebar.Padding = [0 0 0 0];
sidebar.Scrollable = 'on';
panel = uipanel(sidebar,'Title','Simulation inputs','FontWeight','bold');
inputs = uigridlayout(panel,[10 1]);
inputs.RowHeight = {19,30,19,30,19,30,36,34,34,49};
inputs.Padding = [14 12 14 12];
inputs.RowSpacing = 6;

uilabel(inputs,'Text','Bolt configuration');
bolt = uidropdown(inputs,'Items',{'1 bolt (centre)','2 bolts (outer)','3 bolts (all)'}, ...
    'ItemsData',[1 2 3],'Value',1,'Tag','bolts');
uilabel(inputs,'Text','Displacement (mm)');
distance = uieditfield(inputs,'numeric','Value',defaultDistance,'Tag','distance', ...
    'Tooltip','Distance relative to the starting position. Negative values move left.');
uilabel(inputs,'Text','Assumed damping c (N·m·s/rad)');
damping = uieditfield(inputs,'numeric','Value',defaultDamping,'Limits',[0 Inf], ...
    'ValueDisplayFormat','%.6g','Tag','damping');
uilabel(inputs,'Text','Default c is the mean from 63 M3 decay fits. Use zero for an undamped run.', ...
    'WordWrap','on','FontSize',12);
actions = uigridlayout(inputs,[1 2]); actions.Padding = [0 0 0 0];
simulateButton = uibutton(actions,'Text','Simulate','Tag','simulate', ...
    'ButtonPushedFcn',@simulate,'FontWeight','bold', ...
    'BackgroundColor',[0.10 0.36 0.49],'FontColor',[1 1 1]);
uibutton(actions,'Text','Reset','Tag','reset','ButtonPushedFcn',@reset, ...
    'Tooltip','Restore default inputs and clear the simulation.');
batchButton = uibutton(inputs,'Text','Simulate & plot all 63 runs','Tag','batch', ...
    'ButtonPushedFcn',@batch,'FontWeight','bold','Interruptible','off','BusyAction','cancel', ...
    'Tooltip','Sweep 1–3 bolts and 50–70 mm with the current assumed damping. Save each simulated trace.');
profile = uilabel(inputs,'Text',sprintf('M2 cart profile\nAcceleration   2,500 mm/s²\nSpeed limit      175 mm/s'), ...
    'FontSize',12);
profile.Layout.Row = 10;

outputs = uipanel(sidebar,'Title','Simulation outputs','FontWeight','bold','Tag','outputs');
results = uigridlayout(outputs,[5 3]);
results.ColumnWidth = {'1x',68,68};
results.RowHeight = {18,25,25,25,'1x'};
results.Padding = [10 7 10 7]; results.RowSpacing = 4; results.ColumnSpacing = 5;
uilabel(results,'Text','Coefficient','FontWeight','bold');
uilabel(results,'Text','Model','FontWeight','bold','HorizontalAlignment','right', ...
    'Tooltip','Coefficients implied by the assumed c and the selected pivot inertia.');
uilabel(results,'Text','Swing fit','FontWeight','bold','HorizontalAlignment','right', ...
    'Tooltip','Coefficients estimated from the simulated free-swing peaks.');
quantities = {'b   Normalized (s⁻¹)','c   Rotational (N·m·s/rad)','λ   Decay rate (s⁻¹)'};
names = {'b','c','lambda'};
modelLabels = gobjects(3,1); fitLabels = gobjects(3,1);
for k = 1:3
    uilabel(results,'Text',quantities{k},'FontSize',12);
    modelLabels(k) = uilabel(results,'Text','—','HorizontalAlignment','right','Tag',['model_' names{k}]);
    fitLabels(k) = uilabel(results,'Text','—','HorizontalAlignment','right','FontWeight','bold', ...
        'Tag',['fit_' names{k}]);
end
fitInfo = uilabel(results,'Text','','WordWrap','on','FontSize',12,'Tag','fitNote');
fitInfo.Layout.Column = [1 3];
status = uilabel(sidebar,'Text','','WordWrap','on','FontWeight','bold','Tag','status', ...
    'VerticalAlignment','top');

charts = uigridlayout(layout,[5 1]);
charts.RowHeight = {28,'1.4x',30,'1x','1x'};
charts.Padding = [0 0 0 0];
heading = uilabel(charts,'Text','','FontSize',17,'FontWeight','bold');
motionAxes = uiaxes(charts,'Tag','motionAxes');
hold(motionAxes,'on');
axis(motionAxes,'equal');
ylim(motionAxes,[-125 22]); xlim(motionAxes,[-85 135]);
motionAxes.YAxis.Visible = 'off';
motionAxes.Toolbar.Visible = 'off';
disableDefaultInteractivity(motionAxes);
motionAxes.XGrid = 'on';
xlabel(motionAxes,'Position along track (mm)');
rail = plot(motionAxes,[-85 135],[-13 -13],'-','Color',[.50 .54 .56]);
cart = hgtransform('Parent',motionAxes,'Tag','cartPose');
patch('Parent',cart,'XData',[-22 22 22 -22],'YData',[-4 -4 12 12], ...
    'FaceColor',[.12 .53 .63],'EdgeColor',[.07 .31 .39]);
circle = linspace(0,2*pi,25);
for wheelX = [-14 14]
    patch('Parent',cart,'XData',wheelX+4.5*cos(circle),'YData',-8.5+4.5*sin(circle), ...
        'FaceColor',[.27 .31 .33],'EdgeColor',[.55 .60 .63]);
end
pendulum = hgtransform('Parent',cart,'Tag','pendulumPose');
% The supplied STL is in mm, with the shaft at X=Y=0. Render its actual
% face-on geometry; shade recessed surfaces to make the pockets visible.
meshFile = fullfile(folder,'PendulumV2.stl');
if isfile(meshFile)
    body = stlread(meshFile);
    faces = body.ConnectivityList; vertices = body.Points;
    normals = faceNormal(body);
    faces = faces(normals(:,3)>0,:);
    faceHeight = mean(reshape(vertices(faces(:),3),size(faces)),2);
    tone = 0.55+0.45*faceHeight/max(vertices(:,3));
    patch('Parent',pendulum,'Faces',faces,'Vertices',vertices, ...
        'FaceVertexCData',tone.*[.94 .59 .26],'FaceColor','flat', ...
        'EdgeColor','none','Tag','stlBody');
else
    patch('Parent',pendulum,'XData',[-3 3 3 24 24 -24 -24 -3], ...
        'YData',[0 0 -99 -105 -119 -119 -105 -99], ...
        'FaceColor',[.94 .59 .26],'EdgeColor',[.65 .34 .12]);
end
line('Parent',cart,'XData',0,'YData',0,'ZData',13,'Marker','o','MarkerSize',5, ...
    'MarkerFaceColor',[.91 .93 .93],'MarkerEdgeColor',[.24 .26 .28]);
pocketAngles = [-9.5 0 9.5];
pocketX = 112*sind(pocketAngles); pocketY = -112*cosd(pocketAngles);
boltPatterns = logical([0 1 0; 1 0 1; 1 1 1]);
boltsDrawn = line('Parent',pendulum,'XData',0,'YData',-112,'ZData',13,'LineStyle','none', ...
    'Marker','o','MarkerSize',7,'MarkerFaceColor',[.26 .29 .31], ...
    'MarkerEdgeColor',[.86 .85 .79],'Tag','installedBolts');

playBar = uigridlayout(charts,[1 4]);
playBar.Padding = [0 0 0 0]; playBar.ColumnWidth = {70,44,72,'1x'};
replayButton = uibutton(playBar,'Text','Replay','Tag','replay','ButtonPushedFcn',@play);
uilabel(playBar,'Text','Speed');
speed = uidropdown(playBar,'Items',{'0.25×','0.5×','1×'}, ...
    'ItemsData',[.25 .5 1],'Value',1,'Tag','playbackSpeed');
readout = uilabel(playBar,'Text','','HorizontalAlignment','right','Tag','motionReadout');
cartAxes = uiaxes(charts,'Tag','cartAxes');
angleAxes = uiaxes(charts,'Tag','angleAxes');
cartLine = plot(cartAxes,0,0,'Color',[0.02 0.39 0.51],'LineWidth',1.8,'Tag','cartTrace');
angleLine = plot(angleAxes,0,0,'Color',[0.75 0.28 0.10],'LineWidth',1.6,'Tag','swingTrace');
grid(cartAxes,'on'); grid(angleAxes,'on');
ylabel(cartAxes,'Cart position (mm)'); ylabel(angleAxes,'Pendulum angle (deg)');
xlabel(angleAxes,'Time (s)');
title(cartAxes,'Cart displacement'); title(angleAxes,'Pendulum response');
cartMark = xline(cartAxes,0,'--','Cart stops','LabelVerticalAlignment','top');
angleMark = xline(angleAxes,0,'--');
cartDot = line(cartAxes,0,0,'Marker','o','MarkerSize',7,'MarkerFaceColor',cartLine.Color, ...
    'MarkerEdgeColor',[.9 .9 .9],'Tag','cartDot');
angleDot = line(angleAxes,0,0,'Marker','o','MarkerSize',7,'MarkerFaceColor',angleLine.Color, ...
    'MarkerEdgeColor',[.9 .9 .9],'Tag','angleDot');
linkaxes([cartAxes angleAxes],'x');
bolt.ValueChangedFcn = @changed;
distance.ValueChangedFcn = @changed;
damping.ValueChangedFcn = @changed;
frameTimes = []; frameValues = []; playTime = 0; tickClock = [];
player = timer('ExecutionMode','fixedRate','Period',0.04,'BusyMode','drop', ...
    'TimerFcn',@advance,'Name','MS3 cart playback');
fig.DeleteFcn = @disposePlayer;
movegui(fig,'center');
fig.Visible = 'on';
drawnow;
if isvalid(fig), simulate(); end

    function simulate(~,~)
        simulateButton.Enable = 'off';
        try
            config = parameters(parameters.bolt_count==bolt.Value,:);
            trace = MS3_simulate_case(config,distance.Value,damping.Value);
            if any(~isfinite(trace.angle_deg))
                error('MS3:Numerics','These inputs exceed the numerical range of the model.');
            end
            stop(player);
            [frameTimes,uniqueFrames] = unique(trace.time_s);
            frameValues = [trace.cart_mm(uniqueFrames), trace.angle_deg(uniqueFrames)];
            setScene(distance.Value,bolt.Value);
            stopTime = trace.Properties.UserData.stopTime_s;
            coefficients = trace.Properties.UserData.damping;
            for row = 1:3
                modelLabels(row).Text = formatValue(coefficients.Model(row));
                fitLabels(row).Text = formatValue(coefficients.SwingFit(row));
            end
            fitInfo.Text = trace.Properties.UserData.fitNote;
            set(cartLine,'XData',trace.time_s,'YData',trace.cart_mm);
            set(angleLine,'XData',trace.time_s,'YData',trace.angle_deg);
            set([cartMark angleMark],'Value',stopTime,'Visible','on');
            cartAxes.YLimMode = 'auto'; angleAxes.YLimMode = 'auto';
            xlim(cartAxes,[0 trace.time_s(end)]);
            heading.Text = sprintf('%s   |   %g mm',bolt.Items{bolt.ItemsData==bolt.Value},distance.Value);
            status.Text = sprintf('Cart stops at %.3g s.\nFree response shown for 5 s.',stopTime);
            replayButton.Enable = 'on';
            play();
        catch issue
            status.Text = 'Check inputs and simulate again.';
            uialert(fig,issue.message,'Simulation could not run');
        end
        simulateButton.Enable = 'on';
    end

    function batch(~,~)
        batchButton.Enable = 'off';
        status.Text = 'Preparing 63 simulated traces…';
        drawnow limitrate nocallbacks;
        try
            [runTable,runFolder] = MS3_batch(parameters,damping.Value, ...
                fullfile(folder,'Simulated_runs'),@updateProgress);
        catch issue
            if ~isvalid(fig), return; end
            batchButton.Enable = 'on'; status.Text = 'Batch export failed.';
            uialert(fig,issue.message,'Batch export failed');
            return
        end
        if ~isvalid(fig), return; end
        batchButton.Enable = 'on';
        status.Text = 'Saved 63 simulated traces and summary.csv.';
        status.Tooltip = char(runFolder);
        MS3_batch_view(runTable,runFolder);

        function updateProgress(done,total)
            if isvalid(status), status.Text = sprintf('Writing trace %d of %d…',done,total); end
            drawnow limitrate nocallbacks;
        end
    end

    function reset(~,~)
        stop(player);
        frameTimes = []; frameValues = []; playTime = 0;
        replayButton.Enable = 'off'; speed.Value = 1;
        bolt.Value = 1;
        distance.Value = defaultDistance;
        damping.Value = defaultDamping;
        set([modelLabels;fitLabels],'Text','—');
        fitInfo.Text = 'Run a simulation to calculate coefficients.';
        set([cartLine angleLine],'XData',[0 5],'YData',[0 0]);
        set([cartMark angleMark],'Visible','off');
        xlim(cartAxes,[0 5]); ylim(cartAxes,[-1 1]); ylim(angleAxes,[-1 1]);
        setScene(defaultDistance,1);
        showPose(0,0,0);
        heading.Text = 'At rest';
        status.Text = 'Ready to simulate.';
    end

    function changed(~,~)
        status.Text = 'Press Simulate to apply changes.';
    end

    function setScene(targetMm,boltCount)
        % Keep the camera fixed in world coordinates so displacement is visible.
        bounds = [min(0,targetMm)-125, max(0,targetMm)+125];
        xlim(motionAxes,bounds);
        rail.XData = bounds;
        occupied = boltPatterns(boltCount,:);
        set(boltsDrawn,'XData',pocketX(occupied),'YData',pocketY(occupied), ...
            'ZData',13*ones(1,nnz(occupied)));
    end

    function play(~,~)
        if isempty(frameTimes), return; end
        stop(player);
        playTime = 0;
        showPose(0,frameValues(1,1),frameValues(1,2));
        tickClock = tic;
        start(player);
    end

    function advance(~,~)
        if ~isvalid(fig) || isempty(frameTimes), return; end
        elapsed = toc(tickClock);
        tickClock = tic;
        playTime = min(frameTimes(end),playTime + speed.Value*elapsed);
        pose = interp1(frameTimes,frameValues,playTime,'linear');
        showPose(playTime,pose(1),pose(2));
        if playTime >= frameTimes(end), stop(player); end
    end

    function showPose(t,positionMm,angleDeg)
        cart.Matrix = makehgtform('translate',[positionMm 0 0]);
        pendulum.Matrix = makehgtform('zrotate',deg2rad(angleDeg));
        set(cartDot,'XData',t,'YData',positionMm);
        set(angleDot,'XData',t,'YData',angleDeg);
        readout.Text = sprintf('%.2f s    |    x = %.1f mm    |    θ = %.1f°', ...
            t,positionMm,angleDeg);
    end

    function disposePlayer(~,~)
        stop(player);
        delete(player);
    end
end

function label = formatValue(value)
if ~isfinite(value)
    label = '—';
elseif value == 0
    label = '0';
else
    label = sprintf('%.3g',value);
end
end
